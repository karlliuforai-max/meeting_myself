"""商学院板块处理流水线引擎（单产出独立执行）。

核心入口：
  run_one_step(session_id, step_key)  → 生成器，执行单一步骤并 yield 进度事件
  run_stream(session_id, ...)          → 兼容老接口：依次执行全部步骤

每步逻辑独立、产出独立、模型可独立配置（来自会话 step_models 覆盖）。
依赖检查（requires/requires_any）在执行前进行，未满足直接报错事件。
全程注入会话的「补充背景 & 重点要求」(pre_prompt)。
"""
from __future__ import annotations

import math
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterator, List, Optional

from config import settings
from modules import get_module
from modules.base import StepDef
from modules.business_school import prompts
from providers import Message, ProviderError, build_provider, get_provider
from providers import store as provider_store
from storage import persona as persona_store
from storage import session_store

from . import deepnotes, graphcheck, vision
from .transcript import (
    clean_fallback,
    first_timestamp_seconds,
    has_timestamps,
    split_by_length,
    strip_timestamps,
    transcript_duration_seconds,
)

# 各产出文件名（与 business_school/config.py 的 StepDef.output_name 对齐）
OUT_TRANSCRIPT = "实录.md"
OUT_CHAPTERS = "纲目.md"
OUT_MINUTES_CONCISE = "撷要.md"
OUT_MINUTES_DETAILED = "笺注.md"
OUT_GRAPH = "脉络.mmd"

# 唯一的步骤白名单：step_key → 产出文件名。任何对外接受 step 的入口都据此校验，
# 防止越权步骤名穿透到文件/持久层。
STEP_OUTPUTS = {
    "transcript": OUT_TRANSCRIPT,
    "chapters": OUT_CHAPTERS,
    "minutes_concise": OUT_MINUTES_CONCISE,
    "minutes_detailed": OUT_MINUTES_DETAILED,
    "graph": OUT_GRAPH,
}
STEP_KEYS = tuple(STEP_OUTPUTS.keys())

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
# 纲目：分段并行提取候选阶段，再按课堂长度汇编为 20-50 个最终阶段
CHAPTERS_SEG_CHARS = 6000
CHAPTERS_SEGMENT_MAX_TOKENS = 4000
CHAPTERS_FINAL_MAX_TOKENS = 12000
# 绝对下限 4：短课堂（如 30 分钟）不该被硬摊成 20 个阶段；上限仍 50。
CHAPTERS_MIN_STAGES = 4
CHAPTERS_MAX_STAGES = 50
CHAPTERS_MINUTES_PER_STAGE = 6
CHAPTERS_CHARS_PER_STAGE = 1800
# 注意：新一代「思考型」模型的隐藏思考 token 也计入 max_tokens，小额上限会在
# 正文写完前被掐断（真实发生过：导读 800 被截）。以下小额常量均预留思考余量；
# 实际正文长度仍由提示词约束。
GRAPH_MAX_TOKENS = 8000
# 撷要：整篇一次成稿（依赖现代模型长上下文，不再 map-reduce）
MINUTES_CONCISE_MAX_TOKENS = 8000
# 笺注：按纲目分单元生成，单元/导读各有独立上限；整篇长度可观。
# 单元输出上限：反膨胀规则要求「该时段所有具体案例/数据/论断收录或显式舍弃」+ 原话摘引，
# 信息密集单元的产出可逼近切片本身体量（~12K 字）；思考型模型的隐藏思考也计入
# max_tokens。10000 在真实课堂上仍被截断过，16000 起才有余量。
DEEPNOTES_UNIT_MAX_TOKENS = 16000
DEEPNOTES_INTRO_MAX_TOKENS = 4000
# 笺注整篇修订上限：修订须一次吐出整篇长笺注，故给到 16000；
# 若模型输出上限不足触达截断，v0.7 的截断检测会显式报错（属预期行为，提示换大输出上限模型）。
MINUTES_DETAILED_MAX_TOKENS = 16000

# 每步生成时用的输出上限：修订须复用同一档，否则写死的小上限会把实录/笺注截断。
STEP_MAX_TOKENS = {
    "transcript": CORRECT_MAX_TOKENS,
    "chapters": CHAPTERS_FINAL_MAX_TOKENS,
    "minutes_concise": MINUTES_CONCISE_MAX_TOKENS,
    "minutes_detailed": MINUTES_DETAILED_MAX_TOKENS,
    "graph": GRAPH_MAX_TOKENS,
}


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


def resolve_step_models(meta) -> dict:
    """下发每步「最终生效」的模型，供前端展示，去掉前端重复的解析逻辑。

    每步返回 {"provider": id, "model": 最终模型名, "source": "override"|"default"}：
    - override：用户在该产出处手动指定；model 为空则回退该 provider 的默认模型；
    - default：与 _step_default 同一套解析（默认 provider 优先、须已配置）。

    provider 配置在开头快照一次：逐步走 provider_store 会对同一份 providers.json
    反复做磁盘读取 + JSON 解析（每次会话详情 ~15 次），这里收敛为 1 次。
    """
    configs = provider_store.list_configs()
    did = provider_store.default_id()
    by_id = {c["id"]: c for c in configs}
    ordered = sorted(configs, key=lambda c: 0 if c["id"] == did else 1)

    def default_model_of(pid: Optional[str]) -> str:
        cfg = by_id.get(pid or "")
        return (cfg.get("default_model") or "") if cfg else ""

    out: dict = {}
    overrides = meta.step_models or {}
    for step_key in STEP_KEYS:
        ov = overrides.get(step_key) or {}
        if ov.get("provider"):
            pid = ov["provider"]
            out[step_key] = {"provider": pid,
                             "model": ov.get("model") or default_model_of(pid),
                             "source": "override"}
            continue
        sd = _step_def(step_key)
        pref = sd.default_model if sd else ""
        pid, model = (did or None), None
        if pref:
            for c in ordered:
                if pref in (c.get("models") or []) and build_provider(c).is_configured():
                    pid, model = c["id"], pref
                    break
        out[step_key] = {
            "provider": pid or "",
            "model": model or default_model_of(pid),
            "source": "default",
        }
    return out


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


def _call(provider, model, system: str, user: str, *, temperature: float, max_tokens: int,
          allow_truncation: bool = False, on_delta: Optional[Callable[[str], None]] = None) -> str:
    # 仅在需要流式时传 on_delta：非流式路径不带该参数，兼容不声明它的最小实现（如测试桩）。
    extra = {"on_delta": on_delta} if on_delta is not None else {}
    res = provider.chat(
        [Message("system", system), Message("user", user)],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        **extra,
    )
    # 输出触达 max_tokens 被截断：静默保存会写坏产出，除非调用方显式声明可容忍（走各自兜底）。
    if not allow_truncation and getattr(res, "finish_reason", "") == "length":
        raise ProviderError(
            "输出达到 max_tokens 上限被截断，请换更大输出上限的模型，或在模型配置里调高该供应商的输出上限。"
        )
    return res.text.strip()


# ---------- 流式生成进度（S14）：把 chat 放到线程里跑，用 queue 把增量回传成瞬态进度事件 ----------
_STREAM_EVENT_INTERVAL = 2.0  # 节流：最多每 2 秒发一条进度事件


def _streamed_call(provider, model, system: str, user: str, *, step: str, base: int,
                   temperature: float, max_tokens: int, allow_truncation: bool = False):
    """生成器：边生成边 yield 瞬态进度事件，最终 return 文本（调用方用 `text = yield from ...`）。

    进度事件带 transient=True（只推给订阅者、不落盘，见 runner），percent 随累计字数增长封顶 93。
    模型不支持流式时 on_delta 不会被回调，退化为一次性调用，不影响正确结果。
    """
    q: "queue.Queue" = queue.Queue()
    result: dict = {}

    def on_delta(piece: str) -> None:
        q.put(piece)

    def worker():
        try:
            result["text"] = _call(provider, model, system, user, temperature=temperature,
                                   max_tokens=max_tokens, allow_truncation=allow_truncation,
                                   on_delta=on_delta)
        except BaseException as e:  # noqa: BLE001 —— 转交主线程重抛
            result["error"] = e
        finally:
            q.put(None)  # 结束哨兵

    t = threading.Thread(target=worker, name=f"stream-{step}", daemon=True)
    t.start()

    chars = 0
    last_emit = 0.0
    finished = False
    while not finished:
        try:
            piece = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if piece is None:
            finished = True
        else:
            chars += len(piece)
            now = time.time()
            if now - last_emit >= _STREAM_EVENT_INTERVAL:
                last_emit = now
                yield _evt("step", step=step, percent=min(93, base + chars // 120),
                           message=f"已生成约 {chars} 字…", transient=True)
    t.join()
    if "error" in result:
        raise result["error"]
    return result.get("text", "")


def _correct_chunk(provider, model, sys_p: str, chunk: str, *,
                   prev_tail: Optional[str] = None, tries: int = CORRECT_RETRIES):
    """纠错单块，带重试。返回 (文本, 是否降级)。

    送模型前先确定性去时间戳（保留说话人标签作弱提示）。模型多次返回空或报错时，
    用 clean_fallback 兜底（去标签+时间戳的纯正文），保证实录里【绝不残留】原始标记。
    prev_tail：前一原始块（去时间戳后）的结尾，仅作说话人衔接/语境提示，不会被输出。
    """
    cleaned = strip_timestamps(chunk)
    user = prompts.transcript_user(cleaned, prev_tail)
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


def _count_stages(md: str) -> int:
    return len(_STAGE_HEADING.findall(md or ""))


_EMPTY_STAGE_HEADING = re.compile(
    r"(?m)^#{1,6}\s*阶段\s*[0-9０-９一二三四五六七八九十]+\s*[：:]\s*$"
)


def _chapters_complete(md: str) -> bool:
    """判断纲目是否像一个完整成稿。

    目前最常见的截断形态是末尾只剩 `### 阶段20：` 这种空标题；这种结果
    即便阶段数落在 20-50 硬边界内，也不能当成成功产出。
    """
    text = (md or "").strip()
    if not text or _count_stages(text) == 0:
        return False
    return _EMPTY_STAGE_HEADING.search(text) is None


def _sum_part_durations(parts: List[str]) -> Optional[int]:
    """把多个文本文件各自的时长相加（None 跳过）：两个各自从 00:00 起的文件拼接后
    做 max-min 会失真，必须逐文件估时再求和。全部无时间戳时返回 None。"""
    total = 0
    seen = False
    for part in parts:
        d = transcript_duration_seconds(part)
        if d:
            total += d
            seen = True
    return total if seen else None


def _target_chapter_stages(source: str, has_ts: bool, duration: Optional[int] = None) -> int:
    """按课堂时长估算阶段数；无可靠时间戳时用正文长度代理。

    duration 为调用方预计算的整堂课总时长（秒，多文件已逐文件求和）；未提供时
    退回对 source 单体估时（字数代理仍作最终 fallback）。下限 4、上限 50。"""
    if duration is None and has_ts:
        duration = transcript_duration_seconds(source)
    if duration:
        estimate = round(duration / 60 / CHAPTERS_MINUTES_PER_STAGE)
    else:
        content_chars = len(re.sub(r"\s+", "", source or ""))
        estimate = round(content_chars / CHAPTERS_CHARS_PER_STAGE)
    return max(CHAPTERS_MIN_STAGES, min(CHAPTERS_MAX_STAGES, estimate))


def _chapter_stage_range(target: int) -> tuple[int, int]:
    margin = max(2, round(target * 0.15))
    return (
        max(CHAPTERS_MIN_STAGES, target - margin),
        min(CHAPTERS_MAX_STAGES, target + margin),
    )


def _coalesce_segments(segments: list[str], max_segments: int) -> list[str]:
    """相邻合并，确保分段数不超过阶段预算（每段至少能分到 1 个候选阶段）。"""
    if len(segments) <= max_segments:
        return segments
    out: list[str] = []
    total = len(segments)
    for i in range(max_segments):
        start = round(i * total / max_segments)
        end = round((i + 1) * total / max_segments)
        out.append("\n".join(segments[start:end]))
    return out


def _allocate_stage_budgets(segments: list[str], target: int) -> list[int]:
    """按分段长度分配整数阶段配额，总和严格等于 target，且每段至少 1 个。"""
    if not segments:
        return []
    if len(segments) > target:
        raise ValueError("segment count cannot exceed stage target")

    budgets = [1] * len(segments)
    remaining = target - len(segments)
    if remaining <= 0:
        return budgets

    lengths = [max(1, len(seg)) for seg in segments]
    total = sum(lengths)
    raw_shares = [remaining * length / total for length in lengths]
    floors = [math.floor(share) for share in raw_shares]
    budgets = [base + extra for base, extra in zip(budgets, floors)]
    leftover = remaining - sum(floors)
    order = sorted(
        range(len(segments)),
        key=lambda i: raw_shares[i] - floors[i],
        reverse=True,
    )
    for i in order[:leftover]:
        budgets[i] += 1
    return budgets


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
        # 只判存在、不读内容：会话列表/首屏会对每个会话反复调用，读全文太贵。
        if session_store.artifact_exists(sid, new_name):
            found.append(new_name)
            continue
        for legacy in legacies:
            if session_store.artifact_exists(sid, legacy):
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
    return STEP_OUTPUTS[step_key]


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
            yield from _step_minutes_detailed(session_id, meta, pre)
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
        # 修订也注入生效画像：撷要/笺注的「对你的启发」应随画像个性化（脉络/图无此节但注入无害）。
        ptext = persona_store.effective(meta)["text"]
        sys_p = prompts.revise_system(step_def.title, meta.pre_prompt or "", is_graph=is_graph,
                                      persona=ptext)
        user_p = prompts.revise_user(current, instruction)
        # 修订必须复用该步骤生成时的输出上限：写死 4096 会把实录/笺注等长产出截断成坏版本。
        revise_max_tokens = STEP_MAX_TOKENS.get(step_key, MINUTES_CONCISE_MAX_TOKENS)
        revised = yield from _streamed_call(
            prov, model, sys_p, user_p, step=step_key, base=20,
            temperature=0.3, max_tokens=revise_max_tokens,
        )
        if is_graph:
            revised = _extract_required_mermaid(revised)
            # 修订后的脉络同样在写盘前把关连通性：失败报错不写坏图。
            conn = graphcheck.connectivity(revised)
            if not conn["ok"]:
                raise ProviderError(
                    "修订后的脉络连通性校验未通过：存在孤立节点/分支"
                    + (f"（{', '.join(conn['isolated_nodes'][:12])}）" if conn["isolated_nodes"] else "")
                    + "。请调整修订意见或重试。"
                )
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

    # 时间锚点：仅当原始稿本身带时间戳才算，取每块首个时间戳的秒数；否则全 None（老会话无锚点）。
    has_ts = has_timestamps(raw)
    anchors: List[Optional[int]] = [
        (first_timestamp_seconds(c) if has_ts else None) for c in chunks
    ]
    # 上文回顾：块 i 的 prev_tail 取【前一原始块】去时间戳后的结尾 ~400 字。
    # 必须来自原始块而非纠错结果——各块并行执行、纠错结果在调度时尚不存在，
    # 且原始块结尾更稳定；prev_tail 只用于判断说话人衔接，不进入产出。
    prev_tails: List[Optional[str]] = [None] * total
    for i in range(1, total):
        prev_clean = strip_timestamps(chunks[i - 1]).strip()
        prev_tails[i] = prev_clean[-400:] if prev_clean else None

    prov, model = _resolve_provider(meta, "transcript")
    sys_p = prompts.transcript_system(pre)
    results: List[Optional[str]] = [None] * total
    degraded: List[bool] = [False] * total

    def _do(i: int):
        txt, deg = _correct_chunk(prov, model, sys_p, chunks[i], prev_tail=prev_tails[i])
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

    transcript_md = _assemble_transcript(results, anchors)
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
    # 逐文件读一次：既拼接为 raw，又供逐文件估时长（多文件各自从 00:00 起，拼接后 max-min 会失真）
    parts = session_store.read_text_input_parts(sid)
    raw = "\n\n".join(parts).strip()
    if not raw and not transcript_md.strip():
        yield _evt("error", step="chapters", message="未找到转写原文（请上传 txt/md 文件）。")
        return
    has_ts = has_timestamps(raw)
    source = raw if (has_ts and raw.strip()) else (transcript_md or raw)
    duration = _sum_part_durations(parts) if has_ts else None
    target = _target_chapter_stages(source, has_ts, duration)
    yield _evt("step", step="chapters", percent=10,
               message=f"按课堂长度规划约 {target} 个阶段…"
                       + ("（带原文时间戳）" if has_ts else "（原稿无时间戳，时间留空）"))
    prov, model = _resolve_provider(meta, "chapters")
    chapters_md = yield from _make_chapters(prov, model, pre, transcript_md, raw, has_ts, target)
    session_store.write_artifact(sid, OUT_CHAPTERS, chapters_md, note="生成")
    yield _evt("artifact", step="chapters", name=OUT_CHAPTERS)
    yield _evt("step", step="chapters", percent=100, message="章节稿完成")


def _step_minutes(sid: str, meta, pre: str, detail_level: str, out_name: str) -> Iterator[dict]:
    transcript_md = _read_artifact_or_legacy(sid, OUT_TRANSCRIPT)
    chapters_md = _read_artifact_or_legacy(sid, OUT_CHAPTERS)  # 可选
    label = "精炼" if detail_level == "concise" else "详尽"
    step_key = "minutes_concise" if detail_level == "concise" else "minutes_detailed"

    # 收集课堂笔记照片（辅助素材）：转录为文字注入纪要
    notes_md = ""
    n_images = len(session_store.list_image_inputs(sid))
    if n_images:
        yield _evt("step", step=step_key, percent=6,
                   message=f"识别 {n_images} 张笔记照片…")
        notes_md, ninfo = vision.collect_note_text(sid, pre)
        for evt in _note_progress_events(step_key, ninfo):
            yield evt

    yield _evt("step", step=step_key, percent=15, message=f"综合知识、生成{label}版纪要…")
    prov, model = _resolve_provider(meta, step_key)
    # 生效画像（项目级>全局>系统默认）：只在纪要步骤注入，用于个性化「对你的启发」。
    ptext = persona_store.effective(meta)["text"]
    minutes_md = yield from _make_minutes(prov, model, pre, detail_level, step_key,
                                          transcript_md, chapters_md or "", notes_md, ptext)
    session_store.write_artifact(sid, out_name, minutes_md, note="生成")
    yield _evt("artifact", step=step_key, name=out_name)
    yield _evt("step", step=step_key, percent=100, message=f"{label}版纪要完成")


def _gen_deepnotes_unit(prov, model, sys_p: str, unit: dict, unit_index: int, unit_count: int,
                        outline_titles: str) -> dict:
    """生成单个笺注单元并过覆盖率闸门，返回 {阶段号: 该阶段节正文(不含标题行)}。

    覆盖率闸门：本单元每个阶段号必须在输出里找到对应 `### 阶段N` 节；缺失则带反馈重试一次，
    仍缺 → ProviderError（由上层捕获 → 不落盘）。非流式调用，temperature 0.3。
    """
    stage_nums = [s["num"] for s in unit["stages"]]
    unit_stages_md = "\n\n".join(
        (s["heading"] + ("\n" + s["body"] if s.get("body") else "")) for s in unit["stages"]
    )
    user_p = prompts.deepnotes_unit_user(
        unit_stages_md, unit["slice"], outline_titles, unit_index, unit_count
    )
    # 首次调用对瞬时故障（中转站连接抖动/限流）多给一次机会；
    # 截断类错误直接上抛——同参数重试必然再截断，白白翻倍成本。
    try:
        txt = _call(prov, model, sys_p, user_p, temperature=0.3, max_tokens=DEEPNOTES_UNIT_MAX_TOKENS)
    except ProviderError as e:
        if "截断" in str(e):
            raise
        time.sleep(1.0)
        txt = _call(prov, model, sys_p, user_p, temperature=0.3, max_tokens=DEEPNOTES_UNIT_MAX_TOKENS)
    sections = deepnotes.split_stage_sections(txt)
    missing = [n for n in stage_nums if n not in sections]
    if missing:
        # 带反馈重试一次：明确指出缺哪些阶段、标题行须原样。
        feedback = user_p + (
            f"\n\n【上次输出缺少阶段 {('、'.join(str(n) for n in missing))}，"
            "必须包含这些阶段，且每个阶段的标题行（`### 阶段N：…`）原样复用纲目，不得遗漏。】"
        )
        txt = _call(prov, model, sys_p, feedback, temperature=0.3,
                    max_tokens=DEEPNOTES_UNIT_MAX_TOKENS)
        sections = deepnotes.split_stage_sections(txt)
        missing = [n for n in stage_nums if n not in sections]
        if missing:
            raise ProviderError(
                f"笺注单元生成缺少阶段 {('、'.join(str(n) for n in missing))}，"
                "重试后仍未补齐。请重试或切换更强的模型。"
            )
    return {n: sections[n] for n in stage_nums}


# 导读「反问式跑偏」的特征词：中转站偶发丢失 system prompt 时，模型会把纲目当成
# 无指令输入而反问。出现这些特征即判为不合格。
_INTRO_BAD_SIGNS = ("需要我", "您希望", "你希望", "告诉我", "请问", "什么需求", "帮您")


def _intro_acceptable(text: str) -> bool:
    """导读合理性校验：非空、不超长（300 字要求，容忍到 600）、且不是反问式跑偏。"""
    t = (text or "").strip()
    if not t or len(t) > 600:
        return False
    return not any(sign in t for sign in _INTRO_BAD_SIGNS)


def _gen_deepnotes_intro(prov, model, persona_text: str, pre: str, chapters_md: str) -> str:
    """生成导读，带一次重试；两次都不合格返回 ""（拼装容忍无导读，绝不放坏内容进产出）。"""
    for _ in range(2):
        try:
            intro = _call(prov, model, prompts.deepnotes_intro_system(persona_text, pre),
                          prompts.deepnotes_intro_user(chapters_md),
                          temperature=0.3, max_tokens=DEEPNOTES_INTRO_MAX_TOKENS)
        except ProviderError:
            intro = ""
        if _intro_acceptable(intro):
            return intro.strip()
    return ""


def _assemble_deepnotes(title: str, intro: str, items: List[dict], sections: dict,
                        notes_md: str) -> str:
    """确定性拼装笺注：标题 + 【导读】引用块 + 按纲目原顺序回放（part 标题原样、
    stage 用生成节，节序=纲目序、按阶段号取）+ 可选笔记照片附录。"""
    out: List[str] = [f"# 笺注：{title}".rstrip(), ""]
    intro = (intro or "").strip()
    if intro:
        out.append("> **【导读】**")
        out.append(">")
        for ln in intro.splitlines():
            out.append(f"> {ln}" if ln.strip() else ">")
        out.append("")
    for it in items:
        if it.get("kind") == "part":
            out.append(it["heading"].strip())
            out.append("")
        elif it.get("kind") == "stage":
            out.append(it["heading"].strip())
            out.append("")
            body = (sections.get(it["num"]) or "").strip()
            if body:
                out.append(body)
                out.append("")
    notes_md = (notes_md or "").strip()
    if notes_md:
        out.append("## 附录：课堂笔记照片转录")
        out.append("")
        out.append(notes_md)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _step_minutes_detailed(sid: str, meta, pre: str) -> Iterator[dict]:
    """笺注：按纲目逐阶段深度展开。依赖 实录 + 纲目（已由依赖检查保证存在）。

    流程：解析纲目/锚点 → 打包单元 → 并行生成各单元（带覆盖率闸门）→ 生成导读 →
    确定性拼装（part 原位回插、阶段按纲目序回放）→ 附录回填笔记照片转录 → 写盘。
    """
    step_key = "minutes_detailed"
    transcript_md = _read_artifact_or_legacy(sid, OUT_TRANSCRIPT) or ""
    chapters_md = _read_artifact_or_legacy(sid, OUT_CHAPTERS) or ""

    # 笔记照片（辅助素材）：沿用与撷要一致的链路与进度事件。
    notes_md = ""
    n_images = len(session_store.list_image_inputs(sid))
    if n_images:
        yield _evt("step", step=step_key, percent=6, message=f"识别 {n_images} 张笔记照片…")
        notes_md, ninfo = vision.collect_note_text(sid, pre)
        for evt in _note_progress_events(step_key, ninfo):
            yield evt

    items = deepnotes.parse_outline(chapters_md)
    if not any(it.get("kind") == "stage" for it in items):
        # 纲目里解析不到任何阶段（残缺/格式异常）：没有骨架就没有笺注，
        # 静默生成「只有标题和导读」的空壳比报错危害更大。
        raise ProviderError("纲目中解析不到任何阶段标题，无法编纂笺注。请先重新生成「纲目」。")
    anchors = deepnotes.parse_anchors(transcript_md)
    units = deepnotes.group_units(items, transcript_md, anchors)
    outline_titles = _outline_titles(chapters_md)

    prov, model = _resolve_provider(meta, step_key)
    ptext = persona_store.effective(meta)["text"]
    sys_p = prompts.deepnotes_unit_system(ptext, pre)

    yield _evt("step", step=step_key, percent=15,
               message=f"按纲目分 {len(units)} 个单元并行编纂笺注…")

    sections: dict = {}
    unit_count = len(units)
    if unit_count:
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futs = {
                ex.submit(_gen_deepnotes_unit, prov, model, sys_p, unit, idx + 1, unit_count,
                          outline_titles): idx
                for idx, unit in enumerate(units)
            }
            for fut in as_completed(futs):
                part = fut.result()  # 覆盖率不达 → ProviderError 冒泡（上层不落盘）
                sections.update(part)
                done += 1
                yield _evt("step", step=step_key,
                           percent=min(85, 15 + int(70 * done / unit_count)),
                           message=f"笺注单元 {done}/{unit_count}")

    # 导读：全课主线（非流式，短上限）。
    yield _evt("step", step=step_key, percent=88, message="撰写导读…")
    intro = _gen_deepnotes_intro(prov, model, ptext, pre, chapters_md)
    if not intro:
        yield _evt("step", step=step_key, percent=90,
                   message="导读生成两次均不合格，本次省略导读（不影响正文）。")

    minutes_md = _assemble_deepnotes(meta.title, intro, items, sections, notes_md)
    session_store.write_artifact(sid, OUT_MINUTES_DETAILED, minutes_md, note="生成")
    yield _evt("artifact", step=step_key, name=OUT_MINUTES_DETAILED)
    yield _evt("step", step=step_key, percent=100, message="详尽版纪要完成")


def _note_progress_events(step_key: str, ninfo: dict) -> List[dict]:
    """把笔记转录统计转成进度提示事件。"""
    evts: List[dict] = []
    if not ninfo.get("images"):
        return evts
    if not ninfo.get("vision"):
        evts.append(_evt("step", step=step_key, percent=10,
                          message=f"提示：检测到 {ninfo['images']} 张笔记照片，但未配置可用的图片识别模型，"
                                  "本次未纳入。请在右上角「模型配置」面板设置「图片识别模型」后重试。"))
        return evts
    parts = []
    if ninfo.get("transcribed"):
        parts.append(f"新识别 {ninfo['transcribed']} 张")
    if ninfo.get("cached"):
        parts.append(f"复用缓存 {ninfo['cached']} 张")
    if ninfo.get("failed"):
        parts.append(f"{ninfo['failed']} 张识别失败")
    if parts:
        prov = f"（{ninfo.get('provider')}）" if ninfo.get("provider") else ""
        evts.append(_evt("step", step=step_key, percent=12,
                         message="笔记照片：" + "、".join(parts) + prov))
    # 逐张列出失败原因（文件名：原因），并提示可在素材区单张重试/校对——
    # 只报「N 张失败」用户无从判断是限流还是图糊。单条截断、控制总长度。
    failures = ninfo.get("failures") or {}
    if failures:
        lines = [f"「{fn}」：{(reason or '识别失败')[:60]}" for fn, reason in failures.items()]
        detail = "；".join(lines)
        if len(detail) > 600:
            detail = detail[:600] + "…"
        evts.append(_evt("step", step=step_key, percent=13,
                         message="以下笔记照片识别失败，可在素材区对该图单张重新识别或手动校对：\n"
                                 + detail))
    return evts


def _outline_titles(chapters_md: str) -> str:
    """抽取纲目里的部分/阶段标题行（授课顺序参考），供脉络分支左右排序。"""
    lines = []
    for it in deepnotes.parse_outline(chapters_md or ""):
        lines.append(it["heading"].strip())
    return "\n".join(lines)


def _step_graph(sid: str, meta, pre: str) -> Iterator[dict]:
    # 优先用详尽版（笺注）；没有则用精炼版（撷要）；都走 legacy 兼容
    minutes_md = (
        _read_artifact_or_legacy(sid, OUT_MINUTES_DETAILED)
        or _read_artifact_or_legacy(sid, OUT_MINUTES_CONCISE)
    )
    chapters_md = _read_artifact_or_legacy(sid, OUT_CHAPTERS) or ""  # 可无
    outline = _outline_titles(chapters_md)
    yield _evt("step", step="graph", percent=15, message="绘制知识脉络…")
    prov, model = _resolve_provider(meta, "graph")
    sys_p = prompts.graph_system(pre)

    raw_graph = _call(prov, model, sys_p, prompts.graph_user(minutes_md, outline or None),
                      temperature=0.3, max_tokens=GRAPH_MAX_TOKENS)
    graph_code = _extract_required_mermaid(raw_graph)

    # 连通性校验：不通过则带反馈重试一次；仍不通过 → 报错不落盘（绝不写坏图）。
    conn = graphcheck.connectivity(graph_code)
    if not conn["ok"]:
        yield _evt("step", step="graph", percent=55, message="检测到孤立节点/分支，补连通性重试…")
        user_retry = prompts.graph_user(minutes_md, outline or None) + _graph_conn_feedback(conn)
        raw_graph = _call(prov, model, sys_p, user_retry, temperature=0.2, max_tokens=GRAPH_MAX_TOKENS)
        graph_code = _extract_required_mermaid(raw_graph)
        conn = graphcheck.connectivity(graph_code)
        if not conn["ok"]:
            raise ProviderError(
                "脉络连通性校验未通过：仍存在孤立节点/分支"
                + (f"（{', '.join(conn['isolated_nodes'][:12])}）" if conn["isolated_nodes"] else "")
                + "。请重试或切换更强的模型。"
            )

    session_store.write_artifact(sid, OUT_GRAPH, graph_code, note="生成")
    yield _evt("artifact", step="graph", name=OUT_GRAPH)
    yield _evt("step", step="graph", percent=100, message="脉络完成")


def _graph_conn_feedback(conn: dict) -> str:
    """把连通性检查结果拼成给模型的返修反馈。"""
    iso = conn.get("isolated_nodes") or []
    listed = ("：" + "、".join(iso[:20])) if iso else ""
    return (
        "\n\n【上次生成的流程图不连通，存在孤立节点/分支"
        f"{listed}。这些节点/分支必须补上带标注的关系连线（如 -->|递进| / -->|因果| / -->|展开|），"
        "使【全图连通、无任何孤立节点】。请重新输出完整 Mermaid 代码。】"
    )


# ---------- 工具 ----------
def _fmt_anchor(sec: int) -> str:
    """把秒数格式化为锚点小标题时间：有小时 H:MM:SS，无小时 MM:SS。"""
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _assemble_transcript(results, anchors=None) -> str:
    """拼接各块结果为连续正文（块内由模型语义重组发言人、块间空行衔接）。
    results 已是纠错文本或确定性兜底文本，绝不回退到带标签/时间戳的原文。

    anchors：与 results 等长的锚点秒数列表（None=该块无锚点）。有锚点的块前插
    `#### ⏱ H:MM:SS` 小标题，供下游笺注按时段切片；无锚点（老会话/无时间戳）不插。
    """
    if anchors is None:
        anchors = [None] * len(results)
    blocks: List[str] = []
    for i, r in enumerate(results):
        para = (r or "").strip()
        if not para:
            continue
        anchor = anchors[i] if i < len(anchors) else None
        if anchor is not None:
            blocks.append(f"#### ⏱ {_fmt_anchor(anchor)}\n\n{para}")
        else:
            blocks.append(para)
    body = "\n\n".join(blocks).strip()
    return f"# 实录\n\n{body}\n"


def _make_chapters(provider, model, pre: str, transcript_md: str, raw_text: str, has_ts: bool,
                   target: Optional[int] = None):
    """生成器：按课堂长度生成阶段；merge/repair 走流式并 yield 瞬态进度，最终 return 纲目文本。

    - 有时间戳：用【原始稿】分段（时间戳与内容同在），每段细分阶段并标真实时间区间；
    - 无时间戳：退用实录内容分段，时间区间留空。
    先按时长（无时间戳时按字数）计算整堂课阶段预算，再按分段长度分配候选名额；
    最后统一合并跨段重复主题，避免分块数量直接膨胀为最终阶段数量。
    target 由调用方（_step_chapters）预算好传入，避免上下游重复计算两遍。
    """
    source = raw_text if (has_ts and raw_text.strip()) else (transcript_md or raw_text)
    if target is None:
        target = _target_chapter_stages(source, has_ts)
    min_stages, max_stages = _chapter_stage_range(target)
    dynamic_seg_chars = max(CHAPTERS_SEG_CHARS, math.ceil(len(source) / target))
    segs = split_by_length(source, dynamic_seg_chars) or [source]
    segs = _coalesce_segments(segs, target)
    budgets = _allocate_stage_budgets(segs, target)
    seg_sys = prompts.chapters_segment_system(pre, target, min_stages, max_stages)

    def _build_segment(item) -> str:
        index, seg, budget = item
        return _call(
            provider,
            model,
            seg_sys,
            prompts.chapters_user(seg, index + 1, len(segs), budget),
            temperature=0.3,
            max_tokens=CHAPTERS_SEGMENT_MAX_TOKENS,
        )

    outs = _parallel_map(
        _build_segment,
        list(zip(range(len(segs)), segs, budgets)),
    )
    candidates = _renumber_stages("\n\n".join(o.strip() for o in outs if o and o.strip()))
    if _count_stages(candidates) == 0:
        raise ProviderError("纲目候选生成返回空内容，请重试或切换模型。")
    merge_sys = prompts.chapters_merge_system(pre, target, min_stages, max_stages)

    merged = yield from _streamed_call(
        provider, model, merge_sys, prompts.chapters_merge_user(candidates),
        step="chapters", base=40, temperature=0.25, max_tokens=CHAPTERS_FINAL_MAX_TOKENS,
    )
    merged = _renumber_stages(merged)
    merged_count = _count_stages(merged)
    if _chapters_complete(merged) and min_stages <= merged_count <= max_stages:
        return merged

    repaired = yield from _streamed_call(
        provider, model, merge_sys,
        prompts.chapters_merge_user(candidates, observed_count=merged_count),
        step="chapters", base=65, temperature=0.2, max_tokens=CHAPTERS_FINAL_MAX_TOKENS,
    )
    repaired = _renumber_stages(repaired)
    repaired_count = _count_stages(repaired)
    if _chapters_complete(repaired) and min_stages <= repaired_count <= max_stages:
        return repaired

    candidates_count = _count_stages(candidates)
    if _chapters_complete(candidates) and min_stages <= candidates_count <= max_stages:
        return candidates

    # 宽容兜底只允许“偏多但完整”的结果；低于本堂课预算下界通常意味着输出被截断。
    if _chapters_complete(repaired) and max_stages < repaired_count <= CHAPTERS_MAX_STAGES:
        return repaired
    if _chapters_complete(merged) and max_stages < merged_count <= CHAPTERS_MAX_STAGES:
        return merged
    if _chapters_complete(candidates) and max_stages < candidates_count <= CHAPTERS_MAX_STAGES:
        return candidates

    raise ProviderError(
        f"纲目生成未达到完整性要求：目标约 {target} 个阶段，期望 {min_stages}-{max_stages} 个；"
        f"汇编得到 {merged_count} 个，修复后 {repaired_count} 个，候选 {candidates_count} 个。"
        "请重试或切换更大输出上限的模型。"
    )


def _make_minutes(provider, model, pre: str, detail_level: str, step_key: str, transcript_md: str,
                  chapters_md: str, notes_md: str = "", persona: str = ""):
    """生成器：整篇一次成稿（依赖现代模型长上下文），边生成边 yield 瞬态进度、return 文本。

    把完整实录(+纲目+笔记照片转录)一次喂给模型，避免 map-reduce 丢上下文/丢跨段关联。
    超出所选模型上下文时由 provider 直接报错，用户改用大上下文模型即可（不再静默降级）。
    persona：学员画像，注入纪要用于个性化「对你的启发」（空=系统默认，见 prompts._persona）。"""
    system = prompts.minutes_system(pre, detail_level, persona)
    max_tokens = MINUTES_DETAILED_MAX_TOKENS if detail_level == "detailed" else MINUTES_CONCISE_MAX_TOKENS
    return (yield from _streamed_call(
        provider, model, system,
        prompts.minutes_user(transcript_md, chapters_md or None, notes_md or None),
        step=step_key, base=15, temperature=0.4, max_tokens=max_tokens,
    ))


def _extract_mermaid(text: str) -> str:
    t = text.strip()
    if "```" in t:
        m = re.search(r"```(?:mermaid)?\s*(.*?)```", t, re.S)
        if m:
            return m.group(1).strip()
    return t


def _extract_required_mermaid(text: str) -> str:
    graph_code = _extract_mermaid(text)
    if not graph_code.strip():
        raise ProviderError("脉络生成返回空内容，请重试或切换模型。")
    return graph_code


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
