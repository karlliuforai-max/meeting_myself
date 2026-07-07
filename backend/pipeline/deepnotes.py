"""笺注（详尽纪要）解析与切片层。

笺注按【纲目】逐阶段展开：先把纲目解析为「部分 / 阶段」条目，再借助【实录】里的
时间锚点（`#### ⏱ …` 小标题）把每个阶段映射回对应时段的实录切片，最后把连续阶段
贪心打包成若干「单元」并行送模型生成。老会话（实录无锚点 / 纲目无时间）走比例切片兜底。

本模块只做确定性解析/切片/分组，不调用模型；模型调用与拼装在 engine 里。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 纲目里的「部分」宏观标题：## 第N部分：主题
_PART_RE = re.compile(r"^##\s*第\s*[0-9０-９一二三四五六七八九十百]+\s*部分")
# 纲目里的阶段标题：### 阶段N：标题（时间区间）。编号取阿拉伯/全角数字（pipeline 已重排为阿拉伯）。
_STAGE_RE = re.compile(r"^#{2,6}\s*阶段\s*([0-9０-９]+)")
# 实录时间锚点行：#### ⏱ H:MM:SS 或 #### ⏱ MM:SS
_ANCHOR_RE = re.compile(r"(?m)^#{3,6}\s*⏱\s*(\d{1,2}:\d{1,2}(?::\d{1,2})?)")
# 阶段标题行尾部的时间括号（中英文括号皆容忍），内含起止时间
_PAREN_TAIL = re.compile(r"[（(]([^（）()]*)[）)]\s*$")
# 单个时间 token：mm:ss 或 hh:mm:ss
_TIME_TOKEN = re.compile(r"\d{1,2}:\d{1,2}(?::\d{1,2})?")

# 单元切片规模上限（字符）与阶段数上限
DEFAULT_UNIT_MAX_CHARS = 12000
DEFAULT_UNIT_MAX_STAGES = 6


def _norm_digits(s: str) -> str:
    """全角数字转半角。"""
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _token_to_sec(tok: str) -> int:
    """时间 token → 秒。两段视作 MM:SS，三段视作 HH:MM:SS（与 transcript._to_seconds 一致）。"""
    parts = [int(p) for p in tok.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def _parse_time_range(heading: str) -> Tuple[Optional[int], Optional[int]]:
    """从阶段标题行尾部括号里解析 (start_sec, end_sec)。

    括号为空、无括号、或时间不足两段 → 相应位置为 None（容忍 en-dash/连字符/波浪号做分隔）。
    """
    m = _PAREN_TAIL.search(heading.strip())
    if not m:
        return None, None
    inner = m.group(1)
    toks = [t.group(0) for t in _TIME_TOKEN.finditer(inner)]
    if not toks:
        return None, None
    start = _token_to_sec(toks[0])
    end = _token_to_sec(toks[-1]) if len(toks) >= 2 else None
    return start, end


def parse_outline(chapters_md: str) -> List[dict]:
    """把纲目按原文顺序解析为条目列表。

    - 部分：{"kind":"part","heading":原标题行}
    - 阶段：{"kind":"stage","num":N,"heading":原标题行,"body":概括段,"start_sec":int|None,"end_sec":int|None}

    概括段(body)：阶段标题行之后、直到下一个部分/阶段标题之前的正文（去首尾空白）。
    """
    lines = (chapters_md or "").splitlines()
    items: List[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if _PART_RE.match(stripped):
            items.append({"kind": "part", "heading": line.rstrip()})
            i += 1
            continue
        sm = _STAGE_RE.match(stripped)
        if sm:
            num = int(_norm_digits(sm.group(1)))
            start_sec, end_sec = _parse_time_range(stripped)
            # 收集概括段：到下一个部分/阶段标题为止
            body_lines: List[str] = []
            j = i + 1
            while j < n:
                nxt = lines[j].strip()
                if _PART_RE.match(nxt) or _STAGE_RE.match(nxt):
                    break
                body_lines.append(lines[j])
                j += 1
            items.append({
                "kind": "stage",
                "num": num,
                "heading": line.rstrip(),
                "body": "\n".join(body_lines).strip(),
                "start_sec": start_sec,
                "end_sec": end_sec,
            })
            i = j
            continue
        i += 1
    return items


def parse_anchors(transcript_md: str) -> List[Tuple[int, int]]:
    """解析实录里的时间锚点，返回 [(秒数, 字符位置)]，按出现顺序。

    字符位置取锚点行行首在 transcript_md 中的偏移，供切片定位。
    """
    out: List[Tuple[int, int]] = []
    for m in _ANCHOR_RE.finditer(transcript_md or ""):
        out.append((_token_to_sec(m.group(1)), m.start()))
    return out


def slice_for(
    stages: List[dict],
    transcript_md: str,
    anchors: List[Tuple[int, int]],
    total_stages: Optional[int] = None,
) -> str:
    """给一组连续阶段返回对应的实录切片 [start, end)。

    有锚点且这些阶段带时间：
      start = 秒数 ≤ 组内最早 start_sec 的【最后一个】锚点位置（没有则 0）；
      end   = 秒数 ≥ 组内最晚 end_sec 的【第一个】锚点位置（没有则文末）。
    无锚点或阶段无时间：按阶段序号在【全部阶段】中的占比对实录字符数比例切片
      （total_stages 由调用方给出全课阶段总数；缺省时退回本组最大编号）。
    """
    starts = [s["start_sec"] for s in stages if s.get("start_sec") is not None]
    ends = [s["end_sec"] for s in stages if s.get("end_sec") is not None]
    n = len(transcript_md or "")

    if anchors and starts and ends:
        min_start = min(starts)
        max_end = max(ends)
        start_pos = 0
        for sec, pos in anchors:  # 锚点按文档顺序，保留最后一个 sec ≤ min_start 的位置
            if sec <= min_start:
                start_pos = pos
        end_pos = n
        for sec, pos in anchors:  # 第一个 sec ≥ max_end 的位置
            if sec >= max_end:
                end_pos = pos
                break
        if end_pos <= start_pos:  # 极端错序时兜底为文末，避免空切片
            end_pos = n
        return transcript_md[start_pos:end_pos]

    # 比例切片兜底
    nums = [s["num"] for s in stages]
    if total_stages is None:
        total_stages = max(nums, default=1)
    total_stages = max(int(total_stages), 1)
    lo = (min(nums) - 1) / total_stages
    hi = max(nums) / total_stages
    return transcript_md[int(lo * n): int(hi * n)]


def group_units(
    items: List[dict],
    transcript_md: str,
    anchors: List[Tuple[int, int]],
    max_chars: int = DEFAULT_UNIT_MAX_CHARS,
    max_stages: int = DEFAULT_UNIT_MAX_STAGES,
) -> List[dict]:
    """把阶段（忽略 part 条目）按顺序贪心打包为单元。

    单元实录切片超 max_chars 字、或阶段数超 max_stages 就开新单元；每单元至少 1 个阶段。
    part 条目不属于任何单元（拼装时按原位置回插）。返回 [{"stages":[...], "slice":str}]。
    """
    stages = [it for it in items if it.get("kind") == "stage"]
    total = len(stages)
    units: List[dict] = []
    cur: List[dict] = []
    for st in stages:
        trial = cur + [st]
        trial_slice = slice_for(trial, transcript_md, anchors, total_stages=total)
        # cur 非空且加入后越界 → 先收口当前单元，再用当前阶段开新单元
        if cur and (len(trial_slice) > max_chars or len(trial) > max_stages):
            units.append({
                "stages": cur,
                "slice": slice_for(cur, transcript_md, anchors, total_stages=total),
            })
            cur = [st]
        else:
            cur = trial
    if cur:
        units.append({
            "stages": cur,
            "slice": slice_for(cur, transcript_md, anchors, total_stages=total),
        })
    return units


# ---------- 单元输出解析（覆盖率闸门 & 拼装用）----------
_UNIT_STAGE_RE = re.compile(r"(?m)^#{2,6}\s*阶段\s*([0-9０-９]+)")


def split_stage_sections(md: str) -> dict:
    """把单元/整体输出按 `### 阶段N` 切分为 {阶段号: 该节正文(不含标题行)}。

    正文取标题行之后、下一个阶段标题之前的内容（去首尾空白）。用于覆盖率闸门与确定性拼装。
    """
    out: dict = {}
    matches = list(_UNIT_STAGE_RE.finditer(md or ""))
    for idx, m in enumerate(matches):
        num = int(_norm_digits(m.group(1)))
        line_end = md.find("\n", m.start())
        body_start = line_end + 1 if line_end != -1 else len(md)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(md)
        out[num] = md[body_start:end].strip()
    return out
