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
from providers import Message, ProviderError, get_provider
from storage import session_store

from .transcript import build_correction_units, split_by_length

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

CORRECT_MAX_CHARS = 1800
SUMMARY_SINGLE_LIMIT = 16000
SUMMARY_PART_CHARS = 8000
MAX_PARALLEL = 6


def _evt(type_: str, **kw) -> dict:
    kw["type"] = type_
    kw["t"] = round(time.time(), 2)
    return kw


def _resolve_provider(meta, step_key: str):
    """优先用会话级覆盖（step_models[step_key]），否则用全局默认。"""
    override = (meta.step_models or {}).get(step_key, {}) or {}
    name = override.get("provider") or None  # None → store 默认 provider
    provider = get_provider(name)
    return provider, override.get("model")


def _call(provider, model, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
    res = provider.chat(
        [Message("system", system), Message("user", user)],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return res.text.strip()


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

    units, timed = build_correction_units(raw, CORRECT_MAX_CHARS)
    total = len(units)
    yield _evt("step", step="transcript", percent=2,
               message=f"拆为 {total} 块{'（按时间戳）' if timed else ''}，并行纠错中…")

    prov, model = _resolve_provider(meta, "transcript")
    sys_p = prompts.transcript_system(pre)
    results: List[Optional[str]] = [None] * total

    def _do(i: int):
        txt = _call(prov, model, sys_p, prompts.transcript_user(units[i].text),
                    temperature=0.2, max_tokens=4096)
        return i, txt

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futures = [ex.submit(_do, i) for i in range(total)]
        for fut in futures:
            i, txt = fut.result()
            results[i] = txt
            done += 1
            yield _evt("step", step="transcript",
                       percent=2 + int(95 * done / max(total, 1)),
                       message=f"纠错 {done}/{total}")

    transcript_md = _assemble_transcript(units, results, timed)
    session_store.write_artifact(sid, OUT_TRANSCRIPT, transcript_md, note="生成")
    yield _evt("artifact", step="transcript", name=OUT_TRANSCRIPT)
    yield _evt("step", step="transcript", percent=100, message="逐字稿完成")


def _step_chapters(sid: str, meta, pre: str) -> Iterator[dict]:
    transcript_md = _read_artifact_or_legacy(sid, OUT_TRANSCRIPT)
    yield _evt("step", step="chapters", percent=10, message="梳理章节逻辑…")
    prov, model = _resolve_provider(meta, "chapters")
    chapters_md = _summarize(
        prov, model,
        system=prompts.chapters_system(pre),
        build_user=prompts.chapters_user,
        body=transcript_md, max_tokens=2500,
    )
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
def _assemble_transcript(units, results, timed: bool) -> str:
    lines: List[str] = ["# 逐字稿\n"]
    cur_label = None
    for u, txt in zip(units, results):
        txt = txt or u.text
        if timed and u.bucket_label != cur_label:
            cur_label = u.bucket_label
            lines.append(f"\n## [{cur_label}]\n")
        lines.append(txt)
    return "\n".join(lines).strip() + "\n"


def _summarize(provider, model, *, system: str, build_user, body: str, max_tokens: int) -> str:
    if len(body) <= SUMMARY_SINGLE_LIMIT:
        return _call(provider, model, system, build_user(body), temperature=0.4, max_tokens=max_tokens)
    parts = split_by_length(body, SUMMARY_PART_CHARS)
    outs = [_call(provider, model, system, build_user(p), temperature=0.4, max_tokens=max_tokens)
            for p in parts]
    return "\n\n".join(outs)


def _make_minutes(provider, model, pre: str, detail_level: str, transcript_md: str, chapters_md: str) -> str:
    system = prompts.minutes_system(pre, detail_level)
    if len(transcript_md) <= SUMMARY_SINGLE_LIMIT:
        return _call(provider, model, system,
                     prompts.minutes_user(transcript_md, chapters_md or None),
                     temperature=0.4, max_tokens=4096)
    parts = split_by_length(transcript_md, SUMMARY_PART_CHARS)
    map_sys = (
        "从下面这段课堂逐字稿中，提取要点（核心知识点/框架、案例与数据、金句、术语）。"
        "用简洁条目列出，忠于原文，不要杜撰。"
    )
    notes = [_call(provider, model, map_sys, p, temperature=0.3, max_tokens=2000) for p in parts]
    merged = "【各段要点汇总】\n" + "\n\n".join(notes)
    return _call(provider, model, system,
                 prompts.minutes_user(merged, chapters_md or None),
                 temperature=0.4, max_tokens=4096)


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
