"""转写稿解析：时间戳识别、说话人轮次切分、长文本分块。

AI 转写稿常见形态：
  发言人1 00:01:23 / 发言人1：文本 / 主持人：文本 / Speaker 2: text
  [00:01:23] 文本...        / 00:01:23 文本... / (1:23) 文本...
  SRT 风格：00:00:01,000 --> 00:00:04,000 后跟文本

两类下游用途：
  - 实录纠错：按「说话人轮次」切单元（同一说话人连续发言不切断）→ build_correction_units
  - 纲目时间轴：从原始稿逐行取时间锚点 → build_timeline_digest（时间戳可在行首或说话人标签后）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass
class Turn:
    speaker: Optional[str]   # 规范化说话人名；None = 未识别说话人
    seconds: Optional[int]   # 起始秒
    text: str


def _to_seconds(h: Optional[str], m: str, s: str) -> int:
    return (int(h) if h else 0) * 3600 + int(m) * 60 + int(s)


def fmt_ts(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


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


def _join(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    sep = "" if a.endswith(("，", "。", "、", "！", "？", "：", "；")) else " "
    return a + sep + b


def parse_turns(text: str) -> List[Turn]:
    """解析为「说话人轮次」：同一说话人连续发言合并为一轮，切换说话人才开新轮；
    无说话人标签的内容并入当前轮（开头无标签则归入首个匿名轮）。"""
    turns: List[Turn] = []
    cur: Optional[Turn] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or _SRT.match(line):
            continue
        sp, sec, body = _match_speaker(line)
        if sp is not None:
            if cur is not None and cur.speaker == sp:
                cur.text = _join(cur.text, body)  # 同一说话人：保持连续发言完整
            else:
                cur = Turn(speaker=sp, seconds=sec, text=body)
                turns.append(cur)
            continue
        # 非说话人行：剥掉可能的行首时间戳后并入当前轮
        m = _TS.match(line)
        if m and (m.group(1) or m.group(2)):
            sec, body = _to_seconds(m.group(1), m.group(2), m.group(3)), line[m.end():].strip()
        else:
            sec, body = None, line.strip()
        if cur is None:
            cur = Turn(speaker=None, seconds=sec, text=body)
            turns.append(cur)
        else:
            if cur.seconds is None:
                cur.seconds = sec
            cur.text = _join(cur.text, body)
    return [t for t in turns if t.text.strip()]


def build_timeline_digest(text: str, max_anchors: int = 160, snippet_chars: int = 40) -> str:
    """从原始转写稿提取「时间轴锚点」：每行形如 `[HH:MM:SS] 片段开头…`。

    用于让纲目（章节稿）在原始时间戳上对位标注阶段时间区间——因为实录已去掉
    时间戳，这里直接回到原始稿取时间锚点。无任何时间戳时返回空串。
    时间戳既可能在行首（`00:01 文本`），也可能跟在说话人标签后（`发言人1 00:01 文本`），两者都识别。
    锚点过多时按出现顺序均匀抽样，避免 prompt 过长。
    """
    def _snip(body: str) -> str:
        s = " ".join(body.split())
        return s[:snippet_chars] + "…" if len(s) > snippet_chars else s

    anchors: List[tuple] = []  # (秒, 片段开头)
    pending: Optional[int] = None  # 时间戳独占一行、正文在下一行时，暂存等待
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SRT.match(line):
            continue
        sec: Optional[int] = None
        body = line
        sp, ssec, sbody = _match_speaker(line)
        if sp is not None:           # 先剥说话人标签，顺带拿到其后的时间戳
            sec, body = ssec, sbody
        if sec is None:              # 再看（剩余）正文是否以时间戳开头
            m = _TS.match(body)
            if m and (m.group(1) or m.group(2)):
                sec = _to_seconds(m.group(1), m.group(2), m.group(3))
                body = body[m.end():].strip()
        if sec is not None:
            if body:
                anchors.append((sec, _snip(body)))
                pending = None
            else:                    # 时间戳后无正文 → 等下一行的文本作片段
                pending = sec
        elif pending is not None and body:
            anchors.append((pending, _snip(body)))
            pending = None
    if not anchors:
        return ""
    if len(anchors) > max_anchors:
        step = len(anchors) / max_anchors
        anchors = [anchors[int(i * step)] for i in range(max_anchors)]
    return "\n".join(f"[{fmt_ts(sec)}] {snip}" for sec, snip in anchors)


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


@dataclass
class CorrectionUnit:
    """送入纠错模型的最小单元。

    speaker: 该单元首个说话人（None=未识别/纯文本）。
    is_continuation: True 表示本单元是上一单元同一说话人「超长发言」被迫切分出的
        后续片段，组装时应无缝接回上一片段、不另起说话人段落。
    text: 单元正文，含行首说话人标签（如 `发言人1：…`，多轮以空行分隔）。
    """
    speaker: Optional[str]
    is_continuation: bool
    chunk_index: int
    text: str


def _label(speaker: Optional[str]) -> str:
    return f"{speaker}：" if speaker else ""


def _render_turn(t: Turn) -> str:
    return (_label(t.speaker) + t.text).strip()


def build_correction_units(text: str, max_chars: int = 1800) -> tuple[List[CorrectionUnit], bool]:
    """按「说话人轮次」拆纠错单元，返回 (单元列表, 是否识别到说话人)。

    - 永不在一个说话人轮次中间切断；多个短轮次可打包进同一单元（各自保留标签）。
    - 单个轮次超过 max_chars 时才按句子边界切分，后续片标 is_continuation。
    """
    turns = parse_turns(text)
    has_speakers = any(t.speaker for t in turns)
    units: List[CorrectionUnit] = []
    idx = 0
    buf: List[Turn] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len, idx
        if not buf:
            return
        block = "\n\n".join(_render_turn(t) for t in buf)
        units.append(CorrectionUnit(speaker=buf[0].speaker, is_continuation=False,
                                    chunk_index=idx, text=block))
        idx += 1
        buf = []
        buf_len = 0

    for t in turns:
        rendered = _render_turn(t)
        if len(rendered) > max_chars:
            flush()
            label = _label(t.speaker)
            for i, piece in enumerate(split_by_length(t.text, max_chars)):
                text_i = (label + piece).strip() if i == 0 else piece
                units.append(CorrectionUnit(speaker=t.speaker, is_continuation=(i > 0),
                                            chunk_index=idx, text=text_i))
                idx += 1
            continue
        if buf and buf_len + len(rendered) > max_chars:
            flush()
        buf.append(t)
        buf_len += len(rendered) + 2  # +2 ≈ 段间空行
    flush()
    return units, has_speakers
