"""商学院板块处理流水线引擎（单产出独立执行）。

核心入口：
  run_one_step(session_id, step_key)  → 生成器，执行单一步骤并 yield 进度事件
  run_stream(session_id, ...)          → 兼容老接口：依次执行全部步骤

每步逻辑独立、产出独立、模型可独立配置（来自会话 step_models 覆盖）。
依赖检查（requires/requires_any）在执行前进行，未满足直接报错事件。
全程注入会话的「补充背景 & 重点要求」(pre_prompt)。
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator, List, Optional

from config import settings
from modules import get_module
from modules.base import StepDef
from modules.business_school import prompts
from providers import Message, ProviderError, build_provider, get_provider
from providers import store as provider_store
from storage import session_store

from .transcript import clean_fallback, has_timestamps, split_by_length, strip_timestamps

# 各产出文件名（与 business_school/config.py 的 StepDef.output_name 对齐）
OUT_TRANSCRIPT = "实录.md"
OUT_CHAPTERS = "纲目.md"
OUT_MINUTES_CONCISE = "撷要.md"
OUT_MINUTES_DETAILED = "笺注.md"
OUT_GRAPH = "脉络.mmd"

# 兼容老会话：把以前的产出文件名当作各自的别名读取
LEGACY_NAMES = {
    OUT_TRANSCRIPT: ["逐字稿.md"],
    OUT_CHAPTERS: ["章节稿.md"],
    OUT_MINUTES_CONCISE: ["纪要-精炼版.md", "纪要主体.md"],
    OUT_MINUTES_DETAILED: ["纪要-详尽版.md"],
    OUT_GRAPH: ["知识图谱.mmd"],
}

CORRECT_MAX_CHARS = 10000    # 实录纠错分块大小（仍分块：为并行提速 + 抗长生成漂移/返空）
CORRECT_MAX_TOKENS = 16000   # 单块纠错输出上限（须 ≥ 块字数对应 token，避免截断；默认 deepseek-v4-flash 支持 384K 输出）
MAX_PARALLEL = 6
CORRECT_RETRIES = 3          # 单块纠错的尝试次数（模型返回空/报错时重试）
# 纲目分段：每段喂多少原文字符（越小阶段越细），与 v1 的细颗粒度对齐；各段并行生成
CHAPTERS_SEG_CHARS = 6000
CHAPTERS_MAX_TOKENS = 2500
# 撷要/笺注：整篇一次成稿（依赖现代模型长上下文，不再 map-reduce）
MINUTES_CONCISE_MAX_TOKENS = 4096
MINUTES_DETAILED_MAX_TOKENS = 8000


def _evt(type_: str, **kw) -> dict:
    kw["type"] = type_
    kw["t"] = round(time.time(), 2)
    return kw


def _resolve_provider(meta, step_key: str):
    """解析该步骤使用的 provider/model：
    1) 用户在该产出处手动指定（step_models[step_key]）→ 最优先；
    2) 否则用该步骤的智能默认（StepDef.default_model，见 `_step_default`）；
    3) 再不行回退全局默认 provider。"""
    override = (meta.step_models or {}).get(step_key, {}) or {}
    if override.get("provider"):
        return get_provider(override["provider"]), override.get("model")
    pid, model = _step_default(step_key)
    return get_provider(pid), model


def _step_default(step_key: str):
    """步骤未被用户覆盖时的默认 (provider_id, model)。

    在【已配置(有 key)】的 provider 中优先选「提供该步骤首选模型」者（全局默认 provider 优先匹配）；
    找不到（如该模型对应的 provider 没配 key）则回退到全局默认 provider + 其默认模型。
    这样既给出合理默认，又不写死具体 provider id（兼容用户自定义的供应商配置）。
    """
    sd = _step_def(step_key)
    pref = sd.default_model if sd else ""
    if pref:
        did = provider_store.default_id()
        configs = sorted(provider_store.list_configs(), key=lambda c: 0 if c["id"] == did else 1)
        for c in configs:
            if pref in (c.get("models") or []) and build_provider(c).is_configured():
                return c["id"], pref
    return (provider_store.default_id() or None), None


def _parallel_map(fn, items: list) -> list:
    """并行执行 fn(item)，按输入顺序返回结果（并发上限 MAX_PARALLEL）。"""
    results: List[Optional[object]] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for fut in futs:
            results[futs[fut]] = fut.result()
    return results


def _call(provider, model, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
    res = provider.chat(
        [Message("system", system), Message("user", user)],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return res.text.strip()


def _correct_chunk(provider, model, sys_p: str, chunk: str, *, tries: int = CORRECT_RETRIES):
    """纠错单块，带重试。返回 (文本, 是否降级)。

    送模型前先确定性去时间戳（保留说话人标签作弱提示）。模型多次返回空或报错时，
    用 clean_fallback 兜底（去标签+时间戳的纯正文），保证实录里【绝不残留】原始标记。
    """
    cleaned = strip_timestamps(chunk)
    user = prompts.transcript_user(cleaned)
    for attempt in range(tries):
        try:
            txt = _call(provider, model, sys_p, user, temperature=0.2, max_tokens=CORRECT_MAX_TOKENS)
        except ProviderError:
            txt = ""
        if txt.strip():
            return txt, False
        time.sleep(0.5 * (attempt + 1))
    return clean_fallback(chunk), True


# 阶段标题：把跨段拼接后乱掉的「阶段N」统一重排为连续编号
_STAGE_HEADING = re.compile(r"(?m)^#{1,6}\s*阶段\s*[0-9０-９一二三四五六七八九十]+")


def _renumber_stages(md: str) -> str:
    """分段生成的纲目拼接后，按出现顺序把阶段编号重排为 1..K（统一为 `### 阶段N`）。"""
    counter = {"n": 0}

    def repl(_m):
        counter["n"] += 1
        return f"### 阶段{counter['n']}"

    return _STAGE_HEADING.sub(repl, md).strip() + "\n"


# ---------- 依赖检查 ----------
def _read_artifact_or_legacy(sid: str, name: str) -> Optional[str]:
    """优先读新文件名；找不到时尝试老别名。"""
    txt = session_store.read_artifact(sid, name)
    if txt is not None:
        return txt
    for legacy in LEGACY_NAMES.get(name, []):
        txt = session_store.read_artifact(sid, legacy)
        if txt is not None:
            return txt
    return None


def available_artifacts(sid: str) -> List[str]:
    """扫描 artifacts/ 目录，返回当前已有的「规范化新名」列表。
    任何 LEGACY_NAMES 列表里的老文件存在时，对外暴露其对应的新名。
    供前端展示与状态恢复用，避免老会话刚切到新文件名后看不到产出。
    """
    found: List[str] = []
    for new_name, legacies in LEGACY_NAMES.items():
        if session_store.read_artifact(sid, new_name) is not None:
            found.append(new_name)
            continue
        for legacy in legacies:
            if session_store.read_artifact(sid, legacy) is not None:
                found.append(new_name)
                break
    return found


def _step_def(step_key: str) -> Optional[StepDef]:
    mod = get_module("business_school")
    if not mod:
        return None
    for s in mod.steps:
        if s.key == step_key:
            return s
    return None


def _step_output_for_key(step_key: str) -> str:
    mapping = {
        "transcript": OUT_TRANSCRIPT,
        "chapters": OUT_CHAPTERS,
        "minutes_concise": OUT_MINUTES_CONCISE,
        "minutes_detailed": OUT_MINUTES_DETAILED,
        "graph": OUT_GRAPH,
    }
    return mapping[step_key]


def _check_dependencies(sid: str, step_def: StepDef) -> Optional[str]:
    """返回错误消息（依赖未满足）或 None（通过）。"""
    for req in step_def.requires:
        out = _step_output_for_key(req)
        if not _read_artifact_or_legacy(sid, out):
            req_def = _step_def(req)
            return f"需要先生成「{req_def.title if req_def else req}」"
    if step_def.requires_any:
        for req in step_def.requires_any:
            out = _step_output_for_key(req)
            if _read_artifact_or_legacy(sid, out):
                return None
        titles = []
        for req in step_def.requires_any:
            d = _step_def(req)
            titles.append(d.title if d else req)
        return f"需要先生成「{'」或「'.join(titles)}」之一"
    return None


# ---------- 单步执行入口 ----------
def run_one_step(session_id: str, step_key: str) -> Iterator[dict]:
    meta = session_store.get(session_id)
    if not meta:
        yield _evt("error", message="会话不存在")
        return

    step_def = _step_def(step_key)
    if not step_def:
        yield _evt("error", message=f"未知步骤：{step_key}")
        return

    # 预检：模型 provider 是否就绪
    try:
        probe, _ = _resolve_provider(meta, step_key)
        if not probe.is_configured():
            yield _evt("error", message=f"模型「{probe.label}」未配置完整，请在右上角「模型配置」面板补全，或在该产出处切换模型。")
            return
    except ProviderError as e:
        yield _evt("error", message=str(e))
        return

    # 依赖检查
    err = _check_dependencies(session_id, step_def)
    if err:
        yield _evt("error", message=err)
        return

    pre = meta.pre_prompt or ""
    yield _evt("start", step=step_key, message=f"开始：{step_def.title}")

    try:
        if step_key == "transcript":
            yield from _step_transcript(session_id, meta, pre)
        elif step_key == "chapters":
            yield from _step_chapters(session_id, meta, pre)
        elif step_key == "minutes_concise":
            yield from _step_minutes(session_id, meta, pre, "concise", OUT_MINUTES_CONCISE)
        elif step_key == "minutes_detailed":
            yield from _step_minutes(session_id, meta, pre, "detailed", OUT_MINUTES_DETAILED)
        elif step_key == "graph":
            yield from _step_graph(session_id, meta, pre)

        yield _evt("done", step=step_key, message=f"{step_def.title} 完成")
    except ProviderError as e:
        yield _evt("error", step=step_key, message=f"模型调用失败：{e}")
    except Exception as e:  # noqa: BLE001
        yield _evt("error", step=step_key, message=f"处理出错：{e}")


# ---------- 持续迭代修订（基于当前产出 + 用户意见 → 新版本）----------
def revise_one_step(session_id: str, step_key: str, instruction: str) -> Iterator[dict]:
    meta = session_store.get(session_id)
    if not meta:
        yield _evt("error", message="会话不存在")
        return

    step_def = _step_def(step_key)
    if not step_def:
        yield _evt("error", message=f"未知步骤：{step_key}")
        return

    instruction = (instruction or "").strip()
    if not instruction:
        yield _evt("error", step=step_key, message="请填写修订意见。")
        return

    out_name = _step_output_for_key(step_key)
    current = _read_artifact_or_legacy(session_id, out_name)
    if not current:
        yield _evt("error", step=step_key, message=f"「{step_def.title}」尚未生成，无法修订。")
        return

    # 预检模型
    try:
        prov, model = _resolve_provider(meta, step_key)
        if not prov.is_configured():
            yield _evt("error", step=step_key,
                       message=f"模型「{prov.label}」未配置完整，请在右上角「模型配置」面板补全。")
            return
    except ProviderError as e:
        yield _evt("error", step=step_key, message=str(e))
        return

    is_graph = step_key == "graph"
    yield _evt("start", step=step_key, message=f"修订：{step_def.title}")
    yield _evt("step", step=step_key, percent=20, message="按修订意见再生成…")

    try:
        sys_p = prompts.revise_system(step_def.title, meta.pre_prompt or "", is_graph=is_graph)
        user_p = prompts.revise_user(current, instruction)
        revised = _call(prov, model, sys_p, user_p, temperature=0.3, max_tokens=4096)
        if is_graph:
            revised = _extract_mermaid(revised)
        version = session_store.write_artifact(session_id, out_name, revised, note=f"修订：{instruction}")
        yield _evt("artifact", step=step_key, name=out_name)
        yield _evt("step", step=step_key, percent=100, message=f"修订完成（v{version}）")
        yield _evt("done", step=step_key, message=f"{step_def.title} 已更新到 v{version}")
    except ProviderError as e:
        yield _evt("error", step=step_key, message=f"模型调用失败：{e}")
    except Exception as e:  # noqa: BLE001
        yield _evt("error", step=step_key, message=f"修订出错：{e}")


# ---------- 各步骤实现 ----------
def _step_transcript(sid: str, meta, pre: str) -> Iterator[dict]:
    raw = session_store.read_text_inputs(sid)
    if not raw:
        yield _evt("error", step="transcript", message="未找到文本转写稿（请上传 txt/md 文件）。")
        return

    chunks = split_by_length(raw, CORRECT_MAX_CHARS) or [raw]
    total = len(chunks)
    yield _evt("step", step="transcript", percent=2,
               message=f"拆为 {total} 块，并行纠错中…")

    prov, model = _resolve_provider(meta, "transcript")
    sys_p = prompts.transcript_system(pre)
    results: List[Optional[str]] = [None] * total
    degraded: List[bool] = [False] * total

    def _do(i: int):
        txt, deg = _correct_chunk(prov, model, sys_p, chunks[i])
        return i, txt, deg

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futures = [ex.submit(_do, i) for i in range(total)]
        for fut in futures:
            i, txt, deg = fut.result()
            results[i] = txt
            degraded[i] = deg
            done += 1
            yield _evt("step", step="transcript",
                       percent=2 + int(95 * done / max(total, 1)),
                       message=f"纠错 {done}/{total}")

    transcript_md = _assemble_transcript(results)
    session_store.write_artifact(sid, OUT_TRANSCRIPT, transcript_md, note="生成")
    yield _evt("artifact", step="transcript", name=OUT_TRANSCRIPT)
    n_deg = sum(degraded)
    if n_deg:
        yield _evt("step", step="transcript", percent=99,
                   message=f"提示：{n_deg}/{total} 块模型多次返回空，已做基础清洗（去标签/时间戳）"
                           f"但未深度纠错——可对实录单独「重新生成」重试。")
    yield _evt("step", step="transcript", percent=100, message="逐字稿完成")


def _step_chapters(sid: str, meta, pre: str) -> Iterator[dict]:
    # 纲目可独立于实录生成（基于转写原文）：原文优先，实录仅作无时间戳时的备用文本
    transcript_md = _read_artifact_or_legacy(sid, OUT_TRANSCRIPT) or ""
    raw = session_store.read_text_inputs(sid)
    if not raw.strip() and not transcript_md.strip():
        yield _evt("error", step="chapters", message="未找到转写原文（请上传 txt/md 文件）。")
        return
    has_ts = has_timestamps(raw)
    yield _evt("step", step="chapters", percent=10,
               message="分段细梳章节…" + ("（带原文时间戳）" if has_ts else "（原稿无时间戳，时间留空）"))
    prov, model = _resolve_provider(meta, "chapters")
    chapters_md = _make_chapters(prov, model, pre, transcript_md, raw, has_ts)
    session_store.write_artifact(sid, OUT_CHAPTERS, chapters_md, note="生成")
    yield _evt("artifact", step="chapters", name=OUT_CHAPTERS)
    yield _evt("step", step="chapters", percent=100, message="章节稿完成")


def _step_minutes(sid: str, meta, pre: str, detail_level: str, out_name: str) -> Iterator[dict]:
    transcript_md = _read_artifact_or_legacy(sid, OUT_TRANSCRIPT)
    chapters_md = _read_artifact_or_legacy(sid, OUT_CHAPTERS)  # 可选
    label = "精炼" if detail_level == "concise" else "详尽"
    step_key = "minutes_concise" if detail_level == "concise" else "minutes_detailed"

    yield _evt("step", step=step_key, percent=10, message=f"综合知识、生成{label}版纪要…")
    prov, model = _resolve_provider(meta, step_key)
    minutes_md = _make_minutes(prov, model, pre, detail_level, transcript_md, chapters_md or "")
    session_store.write_artifact(sid, out_name, minutes_md, note="生成")
    yield _evt("artifact", step=step_key, name=out_name)
    yield _evt("step", step=step_key, percent=100, message=f"{label}版纪要完成")


def _step_graph(sid: str, meta, pre: str) -> Iterator[dict]:
    # 优先用详尽版（笺注）；没有则用精炼版（撷要）；都走 legacy 兼容
    minutes_md = (
        _read_artifact_or_legacy(sid, OUT_MINUTES_DETAILED)
        or _read_artifact_or_legacy(sid, OUT_MINUTES_CONCISE)
    )
    yield _evt("step", step="graph", percent=15, message="绘制知识脉络…")
    prov, model = _resolve_provider(meta, "graph")
    raw_graph = _call(prov, model, prompts.graph_system(pre), prompts.graph_user(minutes_md),
                      temperature=0.3, max_tokens=2500)
    graph_code = _extract_mermaid(raw_graph)
    session_store.write_artifact(sid, OUT_GRAPH, graph_code, note="生成")
    yield _evt("artifact", step="graph", name=OUT_GRAPH)
    yield _evt("step", step="graph", percent=100, message="脉络完成")


# ---------- 工具 ----------
def _assemble_transcript(results) -> str:
    """拼接各块结果为连续正文（块内由模型语义重组发言人、块间空行衔接）。
    results 已是纠错文本或确定性兜底文本，绝不回退到带标签/时间戳的原文。"""
    paras = [(r or "").strip() for r in results]
    body = "\n\n".join(p for p in paras if p).strip()
    return f"# 实录\n\n{body}\n"


def _make_chapters(provider, model, pre: str, transcript_md: str, raw_text: str, has_ts: bool) -> str:
    """分段生成细颗粒度的纲目（对齐 v1 质量），再统一重排阶段编号。

    - 有时间戳：用【原始稿】分段（时间戳与内容同在），每段细分阶段并标真实时间区间；
    - 无时间戳：退用实录内容分段，时间区间留空。
    每段各自从「阶段1」起编号，拼接后由 `_renumber_stages` 重排成连续序号，
    既保留 v1「分段输入→细分割」的颗粒度，又修掉编号乱、时间不准的问题。
    """
    source = raw_text if (has_ts and raw_text.strip()) else (transcript_md or raw_text)
    seg_sys = prompts.chapters_segment_system(pre)
    segs = split_by_length(source, CHAPTERS_SEG_CHARS) or [source]
    outs = _parallel_map(
        lambda seg: _call(provider, model, seg_sys, prompts.chapters_user(seg),
                          temperature=0.4, max_tokens=CHAPTERS_MAX_TOKENS),
        segs,
    )
    return _renumber_stages("\n\n".join(o.strip() for o in outs if o and o.strip()))


def _make_minutes(provider, model, pre: str, detail_level: str, transcript_md: str, chapters_md: str) -> str:
    """整篇一次成稿（依赖现代模型长上下文）：把完整实录(+纲目)一次喂给模型，
    避免 map-reduce 丢上下文/丢跨段关联。超出所选模型上下文时由 provider 直接报错，
    用户改用大上下文模型即可（不再静默降级）。"""
    system = prompts.minutes_system(pre, detail_level)
    max_tokens = MINUTES_DETAILED_MAX_TOKENS if detail_level == "detailed" else MINUTES_CONCISE_MAX_TOKENS
    return _call(provider, model, system,
                 prompts.minutes_user(transcript_md, chapters_md or None),
                 temperature=0.4, max_tokens=max_tokens)


def _extract_mermaid(text: str) -> str:
    t = text.strip()
    if "```" in t:
        m = re.search(r"```(?:mermaid)?\s*(.*?)```", t, re.S)
        if m:
            return m.group(1).strip()
    return t


# ---------- 兼容老接口：整链生成（依次跑五步）----------
def run_stream(session_id: str, detail_level: str = "detailed") -> Iterator[dict]:
    """兼容入口：依次跑全部步骤（老的"一键生成"）。新前端用 run_one_step。"""
    sequence = ["transcript", "chapters",
                "minutes_concise" if detail_level == "concise" else "minutes_detailed",
                "graph"]
    yield _evt("start", message="开始处理（整链）", steps=sequence)
    for step_key in sequence:
        for evt in run_one_step(session_id, step_key):
            yield evt
            if evt.get("type") == "error":
                return
    yield _evt("done", message="全部完成")
