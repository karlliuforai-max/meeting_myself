"""B1 实录改造：时间锚点识别、锚点小标题插入、上文回顾注入。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from modules.business_school import prompts  # noqa: E402
from pipeline.engine import _assemble_transcript, _fmt_anchor  # noqa: E402
from pipeline.transcript import first_timestamp_seconds  # noqa: E402


# ---------- first_timestamp_seconds 各形态 ----------
def test_first_ts_speaker_head():
    assert first_timestamp_seconds("发言人1 00:01:10 开场白\n继续") == 70


def test_first_ts_line_leading():
    assert first_timestamp_seconds("00:02:00 正文") == 120


def test_first_ts_bracket_mmss():
    # [mm:ss] 无小时 → 分:秒
    assert first_timestamp_seconds("[01:30] 内容") == 90


def test_first_ts_srt():
    assert first_timestamp_seconds("00:00:05,000 --> 00:00:09,000\n字幕") == 5


def test_first_ts_none():
    assert first_timestamp_seconds("这段没有任何时间戳。\n第二行也没有。") is None


def test_first_ts_scans_until_hit():
    # 前几行无时间戳，扫描到带时间戳的行
    text = "纯文字一行\n又一行\n主持人 00:05:00：大家好"
    assert first_timestamp_seconds(text) == 5 * 60


# ---------- _fmt_anchor ----------
def test_fmt_anchor_with_hour():
    assert _fmt_anchor(3 * 3600 + 5 * 60 + 9) == "3:05:09"


def test_fmt_anchor_without_hour():
    assert _fmt_anchor(5 * 60 + 9) == "05:09"


# ---------- _assemble_transcript 锚点插入 ----------
def test_assemble_inserts_anchor_headings():
    md = _assemble_transcript(["第一块", "第二块"], [0, 3600 + 2 * 60])
    assert "# 实录" in md
    assert "#### ⏱ 00:00\n\n第一块" in md
    assert "#### ⏱ 1:02:00\n\n第二块" in md


def test_assemble_no_anchor_when_none():
    md = _assemble_transcript(["甲", "乙"], [None, None])
    assert "⏱" not in md
    assert "甲" in md and "乙" in md


def test_assemble_mixed_anchor_and_none():
    md = _assemble_transcript(["有锚", "无锚"], [30, None])
    assert "#### ⏱ 00:30\n\n有锚" in md
    # 无锚点块直接顺接，不加小标题
    assert "#### ⏱" not in md.split("无锚")[0].split("有锚")[1]


def test_assemble_defaults_to_no_anchors():
    md = _assemble_transcript(["只有正文"])
    assert "⏱" not in md and "只有正文" in md


# ---------- transcript_user 带 prev_tail ----------
def test_transcript_user_with_prev_tail():
    u = prompts.transcript_user("正文片段", prev_tail="上一块结尾")
    assert "上文回顾" in u
    assert "上一块结尾" in u
    assert "正文片段" in u


def test_transcript_user_without_prev_tail():
    u = prompts.transcript_user("正文片段")
    assert "上文回顾" not in u
    assert "正文片段" in u
