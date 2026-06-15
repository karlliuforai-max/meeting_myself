"""转写稿解析：时间戳识别、长文本分块、时间轴摘要。

AI 转写稿常见形态：
  发言人1 00:01:23 / 发言人1：文本 / 主持人：文本 / Speaker 2: text
  [00:01:23] 文本...        / 00:01:23 文本... / (1:23) 文本...
  SRT 风格：00:00:01,000 --> 00:00:04,000 后跟文本

下游用途：
  - 实录纠错：按句子边界切块（`split_by_length`）→ 模型自行语义重组发言人（原稿发言人标签
    由机器识别、常不准，不作为切分依据）。送模型前用 `strip_timestamps` 确定性去时间戳；
    模型多次返回空时用 `clean_fallback` 兜底（去标签+时间戳），避免原文残留。
  - 纲目：直接用带时间戳的原始稿分段生成（`has_timestamps` 判断能否标真实时间区间）。
"""
from __future__ import annotations

import re
from typing import List, Optional

# 行首时间戳：[h:mm:ss] / h:mm:ss / (mm:ss) / mm:ss，允许方括号/圆括号包裹
_TS = re.compile(
    r"^\s*[\[(]?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,]\d{1,3})?\s*[\])]?\s*"
)
# SRT 时间轴行
_SRT = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->")

# 说话人标签（行首）。强关键词可带编号/时间戳，冒号可选；为降低误判，
# 仅当出现「编号 / 时间戳 / 冒号」之一时才认定为说话人头。
_SP_WORDS = (
    r"发言人|说话人|讲者|講者|主讲人|主持人|嘉宾|提问人|提问者|"
    r"观众|听众|老师|学员|學員|同学|Speaker"
)
_KW_RE = re.compile(rf"^\s*(?:{_SP_WORDS})", re.IGNORECASE)
# 说话人编号：关键词后的 1-3 位数字，但不能是时间戳的一段（即后面不接「更多数字」或「ASCII 冒号+两位数字」）
_NUM_RE = re.compile(r"^\s*(\d{1,3})(?!\d)(?!:\d{2})")
# 字母代号说话人（A / B1），后续必须带冒号才认定
_CODE_RE = re.compile(r"^\s*([A-Za-z]\d{0,2})")
# 行首时间戳 / 冒号（用于剥离说话人标签后残留的标记，顺序不限）
_LEAD_TS = re.compile(r"^\s*[\[(]?\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?\s*[\])]?")
_LEAD_COLON = re.compile(r"^\s*[:：]")
# 从时间戳子串里取秒
_TS_INNER = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})")
# 内联时间戳（任意位置）：用于确定性删除
_INLINE_TS = re.compile(r"[\[(]?\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?\s*[\])]?")


def _to_seconds(h: Optional[str], m: str, s: str) -> int:
    return (int(h) if h else 0) * 3600 + int(m) * 60 + int(s)


def _ts_to_sec(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = _TS_INNER.search(s)
    return _to_seconds(m.group(1), m.group(2), m.group(3)) if m else None


def _strip_markers(rest: str):
    """剥掉开头的（时间戳 / 冒号）任意组合，返回 (秒, 是否出现过冒号, 剩余正文)。"""
    sec: Optional[int] = None
    saw_colon = False
    while True:
        before = rest
        mt = _LEAD_TS.match(rest)
        if mt:
            if sec is None:
                sec = _ts_to_sec(mt.group(0))
            rest = rest[mt.end():]
        mc = _LEAD_COLON.match(rest)
        if mc:
            saw_colon = True
            rest = rest[mc.end():]
        if rest == before:
            break
    return sec, saw_colon, rest.strip()


def _match_speaker(line: str):
    """识别行首说话人头。返回 (规范化说话人名 or None, 起始秒 or None, 去标签/时间戳后的正文)。

    支持：发言人1 / 发言人 1 / 主持人：/ 发言人2：00:01:10 / 主持人 00:02:00 / Speaker 1: / A：
    为降低误判，关键词必须伴随「编号 / 时间戳 / 冒号」之一才算说话人头（纯「老师」开头的句子不算）；
    字母代号必须带冒号。
    """
    m = _KW_RE.match(line)
    if m:
        sp = re.sub(r"\s+", "", m.group(0))
        rest = line[m.end():]
        mn = _NUM_RE.match(rest)
        if mn:
            sp += mn.group(1)
            rest = rest[mn.end():]
        sec, saw_colon, body = _strip_markers(rest)
        if mn or sec is not None or saw_colon:
            return sp, sec, body
        return None, None, line
    m = _CODE_RE.match(line)
    if m:
        sec, saw_colon, body = _strip_markers(line[m.end():])
        if saw_colon:  # 字母代号必须带冒号才认定为说话人
            return re.sub(r"\s+", "", m.group(1)), sec, body
    return None, None, line


def strip_timestamps(text: str) -> str:
    """删除时间戳（行首/内联/SRT 行），保留说话人标签与正文。

    实录纠错前的确定性预清洗：无论模型是否配合，时间戳都不会残留到实录。
    保留说话人标签（如「发言人1」）作为模型判断说话人切换的弱提示。
    """
    out: List[str] = []
    for raw in text.splitlines():
        if _SRT.match(raw):
            continue
        out.append(_INLINE_TS.sub("", raw))
    return "\n".join(out)


def clean_fallback(text: str) -> str:
    """确定性兜底清洗：删除说话人标签 + 时间戳，仅留正文逐行拼接。

    当模型多次返回空、无法纠错时使用，避免把带标签/时间戳的原文直接灌进实录。
    """
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SRT.match(line):
            continue
        sp, _sec, body = _match_speaker(line)
        if sp is None:
            body = line
        body = _INLINE_TS.sub("", body).strip()
        if body:
            out.append(body)
    return "\n".join(out)


def has_timestamps(text: str) -> bool:
    """原始稿是否含时间戳（行首或说话人标签后）。决定纲目能否标注真实时间区间。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SRT.match(line):
            continue
        sp, ssec, sbody = _match_speaker(line)
        if ssec is not None:
            return True
        body = sbody if sp is not None else line
        m = _TS.match(body)
        if m and (m.group(1) or m.group(2)):
            return True
    return False


def transcript_duration_seconds(text: str) -> Optional[int]:
    """根据原稿里的首末时间戳估算课堂时长；不足两个时间点时返回 None。"""
    seconds: List[int] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _SRT.match(line):
            seconds.extend(
                _to_seconds(m.group(1), m.group(2), m.group(3))
                for m in _TS_INNER.finditer(line)
            )
            continue

        sp, speaker_sec, speaker_body = _match_speaker(line)
        if speaker_sec is not None:
            seconds.append(speaker_sec)
        body = speaker_body if sp is not None else line
        m = _TS.match(body)
        if m:
            seconds.append(_to_seconds(m.group(1), m.group(2), m.group(3)))

    if len(seconds) < 2:
        return None
    duration = max(seconds) - min(seconds)
    return duration if duration > 0 else None


def split_by_length(text: str, max_chars: int = 1800) -> List[str]:
    """按句子边界把长文本切成不超过 max_chars 的块。"""
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    # 在中英文句末标点/换行处断句
    pieces = re.split(r"(?<=[。！？!?\n])", text)
    chunks: List[str] = []
    buf = ""
    for p in pieces:
        if len(buf) + len(p) > max_chars and buf:
            chunks.append(buf)
            buf = p
        else:
            buf += p
    if buf.strip():
        chunks.append(buf)
    return chunks
