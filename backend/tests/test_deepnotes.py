"""B3 笺注重构：纲目/锚点解析、切片映射、单元分组、覆盖率闸门、确定性拼装。"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pipeline.engine as engine  # noqa: E402
from pipeline import deepnotes  # noqa: E402
from providers import ProviderError  # noqa: E402
from storage.session_store import SessionStore  # noqa: E402


# ---------- parse_outline ----------
def test_parse_outline_parts_stages_and_times():
    md = (
        "## 第1部分：主题A\n"
        "### 阶段1：引入（00:00–01:00）\n"
        "概括1\n"
        "### 阶段2：中段（）\n"
        "概括2\n"
        "## 第2部分：主题B\n"
        "### 阶段3：结尾\n"
        "概括3\n"
    )
    items = deepnotes.parse_outline(md)
    kinds = [it["kind"] for it in items]
    assert kinds == ["part", "stage", "stage", "part", "stage"]
    # 第一个阶段：en-dash 时间括号解析
    s1 = items[1]
    assert s1["num"] == 1 and s1["start_sec"] == 0 and s1["end_sec"] == 60
    assert s1["body"] == "概括1"
    # 空括号 → 无时间
    s2 = items[2]
    assert s2["num"] == 2 and s2["start_sec"] is None and s2["end_sec"] is None
    # 无括号 → 无时间
    s3 = items[4]
    assert s3["num"] == 3 and s3["start_sec"] is None and s3["end_sec"] is None
    assert items[0]["heading"] == "## 第1部分：主题A"


def test_parse_outline_hyphen_and_hms():
    md = "### 阶段1：x (01:00-02:30)\n正文\n### 阶段2：y（00:23:56~00:25:00）"
    items = deepnotes.parse_outline(md)
    assert items[0]["start_sec"] == 60 and items[0]["end_sec"] == 150
    # HH:MM:SS
    assert items[1]["start_sec"] == 23 * 60 + 56
    assert items[1]["end_sec"] == 25 * 60


# ---------- parse_anchors ----------
def test_parse_anchors_positions_and_secs():
    md = "# 实录\n\n#### ⏱ 00:00\n\nAAAA\n\n#### ⏱ 1:02:00\n\nBBBB\n"
    anchors = deepnotes.parse_anchors(md)
    assert [sec for sec, _ in anchors] == [0, 3600 + 2 * 60]
    # 位置指向锚点行行首
    for sec, pos in anchors:
        assert md[pos:pos + 5] == "#### "


# ---------- slice_for ----------
def _stage(num, start=None, end=None):
    return {"kind": "stage", "num": num, "heading": f"### 阶段{num}：t",
            "body": "", "start_sec": start, "end_sec": end}


def test_slice_for_anchor_mapping():
    md = "#### ⏱ 00:00\nAAAA\n#### ⏱ 01:00\nBBBB\n#### ⏱ 02:00\nCCCC\n"
    anchors = deepnotes.parse_anchors(md)
    sl = deepnotes.slice_for([_stage(2, 60, 120)], md, anchors)
    assert "BBBB" in sl and "AAAA" not in sl and "CCCC" not in sl


def test_slice_for_anchor_multi_stage_span():
    md = "#### ⏱ 00:00\nAAAA\n#### ⏱ 01:00\nBBBB\n#### ⏱ 02:00\nCCCC\n"
    anchors = deepnotes.parse_anchors(md)
    sl = deepnotes.slice_for([_stage(1, 0, 60), _stage(2, 60, 120)], md, anchors)
    assert "AAAA" in sl and "BBBB" in sl and "CCCC" not in sl


def test_slice_for_proportional_without_anchors():
    md = "x" * 100
    sl = deepnotes.slice_for([_stage(1)], md, [], total_stages=4)
    assert sl == md[0:25]
    sl2 = deepnotes.slice_for([_stage(2)], md, [], total_stages=4)
    assert sl2 == md[25:50]


def test_slice_for_proportional_when_no_time_even_with_anchors():
    # 有锚点但阶段无时间 → 退回比例切片
    md = "y" * 80
    anchors = [(0, 0), (60, 40)]
    sl = deepnotes.slice_for([_stage(1)], md, anchors, total_stages=2)
    assert sl == md[0:40]


# ---------- group_units ----------
def test_group_units_single_huge_stage_stays_one_unit():
    md = "z" * 20000
    items = [_stage(1)]
    units = deepnotes.group_units(items, md, [], max_chars=12000, max_stages=6)
    assert len(units) == 1
    assert units[0]["stages"][0]["num"] == 1


def test_group_units_six_stage_cap():
    md = "w" * 60
    items = [_stage(i) for i in range(1, 8)]  # 7 阶段
    units = deepnotes.group_units(items, md, [], max_chars=12000, max_stages=6)
    assert [len(u["stages"]) for u in units] == [6, 1]


def test_group_units_char_cap_with_anchors():
    # 每个阶段切片约 8000 字，第二个阶段加入即超 12000 → 拆单元
    body = "a" * 8000 + "b" * 8000
    md = f"#### ⏱ 00:00\n{'a' * 8000}\n#### ⏱ 01:00\n{'b' * 8000}\n"
    anchors = deepnotes.parse_anchors(md)
    items = [_stage(1, 0, 60), _stage(2, 60, 3600)]
    units = deepnotes.group_units(items, md, anchors, max_chars=12000, max_stages=6)
    assert len(units) == 2


# ---------- 覆盖率闸门（_gen_deepnotes_unit）----------
class _UnitProvider:
    """按 user 里出现的阶段号生成对应节；miss_first 指定首次调用要漏掉的阶段号。"""

    def __init__(self, miss_first=None):
        self.miss_first = set(miss_first or [])
        self.calls = 0

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096, on_delta=None):
        self.calls += 1
        user = messages[1].content
        seen = []
        for x in re.findall(r"阶段\s*(\d+)", user):
            n = int(x)
            if n not in seen:
                seen.append(n)
        omit = self.miss_first if self.calls == 1 else set()
        parts = [f"### 阶段{n}：主题{n}\n**核心论点**\n正文{n}" for n in seen if n not in omit]
        return SimpleNamespace(text="\n\n".join(parts), model=model or "m",
                               provider="p", usage={})


def _unit(nums):
    return {"stages": [_stage(n) for n in nums], "slice": "实录切片"}


def test_gen_unit_retry_then_complete():
    prov = _UnitProvider(miss_first=[2])
    got = engine._gen_deepnotes_unit(prov, "m", "sys", _unit([1, 2]), 1, 1, "### 阶段1\n### 阶段2")
    assert set(got.keys()) == {1, 2}
    assert prov.calls == 2  # 首次缺阶段2 → 重试一次补齐


def test_gen_unit_two_misses_raises():
    prov = _UnitProvider(miss_first=[2])
    prov.miss_first = {2}
    # 让两次都缺：把 miss 固定为始终生效
    prov.chat_orig = prov.chat

    class AlwaysMiss(_UnitProvider):
        def chat(self, messages, model=None, temperature=0.3, max_tokens=4096, on_delta=None):
            self.calls += 1
            user = messages[1].content
            parts = [f"### 阶段{n}：t\n正文" for n in {int(x) for x in re.findall(r"阶段\s*(\d+)", user)} if n != 2]
            return SimpleNamespace(text="\n\n".join(sorted(parts)), model="m", provider="p", usage={})

    p2 = AlwaysMiss()
    try:
        engine._gen_deepnotes_unit(p2, "m", "sys", _unit([1, 2]), 1, 1, "### 阶段1\n### 阶段2")
        assert False, "should raise"
    except ProviderError as e:
        assert "阶段" in str(e)
    assert p2.calls == 2


# ---------- 确定性拼装 ----------
def test_assemble_preserves_parts_and_appendix():
    items = deepnotes.parse_outline(
        "## 第1部分：开篇\n### 阶段1：引入\n概括1\n## 第2部分：深入\n### 阶段2：应用\n概括2\n"
    )
    sections = {1: "**核心论点**\n甲论点", 2: "**核心论点**\n乙论点"}
    md = engine._assemble_deepnotes("测试课", "这是导读正文。", items, sections, "### 笔记照片：n.png\n\n手写内容")
    assert md.startswith("# 笺注：测试课")
    assert "【导读】" in md and "这是导读正文" in md
    # part 标题原样、顺序正确
    i_part1 = md.index("## 第1部分：开篇")
    i_stage1 = md.index("### 阶段1：引入")
    i_part2 = md.index("## 第2部分：深入")
    i_stage2 = md.index("### 阶段2：应用")
    assert i_part1 < i_stage1 < i_part2 < i_stage2
    assert "甲论点" in md and "乙论点" in md
    # 笔记附录
    assert "## 附录：课堂笔记照片转录" in md
    assert "手写内容" in md


def test_assemble_no_appendix_when_no_notes():
    items = deepnotes.parse_outline("### 阶段1：x\n概括\n")
    md = engine._assemble_deepnotes("课", "导读", items, {1: "正文"}, "")
    assert "附录" not in md


# ---------- 引擎流程集成（覆盖率 + 拼装 + 老会话无锚点）----------
class DeepnotesStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))
        self.meta = self.store.create("business_school", "商业模式课")
        self.store.write_artifact(
            self.meta.id, "实录.md",
            "# 实录\n\n#### ⏱ 00:00\n\n正文A讲了苹果公司。\n\n#### ⏱ 01:00\n\n正文B讲了增长。\n",
            note="seed")
        self.store.write_artifact(
            self.meta.id, "纲目.md",
            "## 第1部分：开篇\n### 阶段1：引入（00:00–01:00）\n概括1\n"
            "## 第2部分：深入\n### 阶段2：增长（01:00–02:00）\n概括2\n",
            note="seed")
        self.orig_store = engine.session_store
        self.orig_resolve = engine._resolve_provider
        engine.session_store = self.store
        engine._resolve_provider = lambda meta, step: (_DeepProvider(), "m")

    def tearDown(self):
        engine.session_store = self.orig_store
        engine._resolve_provider = self.orig_resolve
        self.tmp.cleanup()

    def test_detailed_flow_assembles_by_outline(self):
        events = list(engine.run_one_step(self.meta.id, "minutes_detailed"))
        self.assertEqual(events[-1]["type"], "done")
        md = self.store.read_artifact(self.meta.id, "笺注.md")
        self.assertIsNotNone(md)
        self.assertIn("# 笺注：商业模式课", md)
        self.assertIn("## 第1部分：开篇", md)
        self.assertIn("## 第2部分：深入", md)
        self.assertIn("### 阶段1：引入", md)
        self.assertIn("### 阶段2：增长", md)
        self.assertLess(md.index("## 第1部分"), md.index("## 第2部分"))


class _DeepProvider:
    label = "Deep"

    def is_configured(self):
        return True

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096, on_delta=None):
        system, user = messages[0].content, messages[1].content
        if "导读" in system:
            return SimpleNamespace(text="全课从苹果讲到增长。", model="m", provider="p", usage={})
        seen = []
        for x in re.findall(r"阶段\s*(\d+)", user):
            n = int(x)
            if n not in seen:
                seen.append(n)
        parts = [f"### 阶段{n}：T{n}\n**核心论点**\n正文{n}" for n in seen]
        return SimpleNamespace(text="\n\n".join(parts), model="m", provider="p", usage={})


if __name__ == "__main__":
    unittest.main()
