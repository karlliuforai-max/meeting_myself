"""转写稿解析：时间戳识别、10 分钟分桶、长文本分块。

AI 转写稿常见形态：
  [00:01:23] 文本...        / 00:01:23 文本... / (1:23) 文本...
  SRT 风格：00:00:01,000 --> 00:00:04,000 后跟文本
若识别不到任何时间戳，则按纯文本处理（不做时间分桶）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# 行首时间戳：[h:mm:ss] / h:mm:ss / (mm:ss) / mm:ss，允许方括号/圆括号包裹
_TS = re.compile(
    r"^\s*[\[(]?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,]\d{1,3})?\s*[\])]?\s*"
)
# SRT 时间轴行
_SRT = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->")

BUCKET_SECONDS = 600  # 10 分钟


@dataclass
class Segment:
    seconds: Optional[int]  # 起始秒；None = 无时间戳
    text: str


@dataclass
class Bucket:
    start: int               # 桶起始秒
    end: int                 # 桶结束秒
    text: str
    label: str = ""          # 形如 00:00–10:00


def _to_seconds(h: Optional[str], m: str, s: str) -> int:
    return (int(h) if h else 0) * 3600 + int(m) * 60 + int(s)


def fmt_ts(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_segments(text: str) -> List[Segment]:
    """逐行解析为带（可选）时间戳的片段。"""
    segs: List[Segment] = []
    cur: Optional[Segment] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _SRT.match(line):
            continue  # 跳过 SRT 时间轴行本身
        m = _TS.match(line)
        if m and (m.group(1) or m.group(2)):
            sec = _to_seconds(m.group(1), m.group(2), m.group(3))
            body = line[m.end():].strip()
            cur = Segment(seconds=sec, text=body)
            segs.append(cur)
        else:
            if cur is None:
                cur = Segment(seconds=None, text=line.strip())
                segs.append(cur)
            else:
                cur.text += ("" if cur.text.endswith(("，", "。", "、")) else " ") + line.strip()
    return segs


def has_timestamps(segs: List[Segment]) -> bool:
    return any(s.seconds is not None for s in segs)


def group_10min(segs: List[Segment]) -> List[Bucket]:
    """按 10 分钟把片段分桶。无时间戳的片段并入前一个桶。"""
    buckets: dict[int, List[str]] = {}
    last_idx = 0
    for s in segs:
        if s.seconds is not None:
            last_idx = s.seconds // BUCKET_SECONDS
        buckets.setdefault(last_idx, []).append(s.text)
    out: List[Bucket] = []
    for idx in sorted(buckets):
        start = idx * BUCKET_SECONDS
        end = start + BUCKET_SECONDS
        out.append(
            Bucket(
                start=start,
                end=end,
                text="\n".join(buckets[idx]).strip(),
                label=f"{fmt_ts(start)}–{fmt_ts(end)}",
            )
        )
    return out


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
    """送入纠错模型的最小单元：归属某个时间桶的一段文本。"""
    bucket_label: str        # 空串 = 纯文本模式
    chunk_index: int
    text: str


def build_correction_units(text: str, max_chars: int = 1800) -> tuple[List[CorrectionUnit], bool]:
    """把原始转写稿拆成纠错单元，返回 (单元列表, 是否带时间戳)。"""
    segs = parse_segments(text)
    timed = has_timestamps(segs)
    units: List[CorrectionUnit] = []
    if timed:
        for b in group_10min(segs):
            for i, chunk in enumerate(split_by_length(b.text, max_chars)):
                units.append(CorrectionUnit(bucket_label=b.label, chunk_index=i, text=chunk))
    else:
        for i, chunk in enumerate(split_by_length(text, max_chars)):
            units.append(CorrectionUnit(bucket_label="", chunk_index=i, text=chunk))
    return units, timed
