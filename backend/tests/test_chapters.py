from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from pipeline.engine import (  # noqa: E402
    _allocate_stage_budgets,
    _count_stages,
    _make_chapters,
    _target_chapter_stages,
)
from pipeline.transcript import transcript_duration_seconds  # noqa: E402


def _outline(count: int, empty_last: bool = False) -> str:
    rows = []
    for i in range(1, count + 1):
        if empty_last and i == count:
            rows.append(f"### 阶段{i}：")
        else:
            rows.append(f"### 阶段{i}：主题{i} ()\n第{i}阶段的核心内容。")
    return "\n\n".join(rows)


class FakeProvider:
    def __init__(self, invalid_first_merge: bool = False):
        self.invalid_first_merge = invalid_first_merge
        self.merge_calls = 0
        self.calls = []

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        system = messages[0].content
        user = messages[1].content
        self.calls.append((system, user, max_tokens))

        if "【候选纲目】" in user:
            self.merge_calls += 1
            if self.invalid_first_merge and self.merge_calls == 1:
                count = 60
            else:
                count = int(re.search(r"阶段总数以 (\d+) 个", system).group(1))
        else:
            count = int(re.search(r"恰好 (\d+) 个", user).group(1))

        return SimpleNamespace(
            text=_outline(count),
            model=model or "fake",
            provider="fake",
            usage={},
        )


class TruncatedMergeProvider(FakeProvider):
    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        system = messages[0].content
        user = messages[1].content
        self.calls.append((system, user, max_tokens))

        if "【候选纲目】" in user:
            self.merge_calls += 1
            return SimpleNamespace(
                text=_outline(20, empty_last=True),
                model=model or "fake",
                provider="fake",
                usage={},
            )

        count = int(re.search(r"恰好 (\d+) 个", user).group(1))
        return SimpleNamespace(
            text=_outline(count),
            model=model or "fake",
            provider="fake",
            usage={},
        )


class ChapterBudgetTests(unittest.TestCase):
    def test_duration_uses_first_and_last_timestamp(self):
        text = "发言人1 00:00:00 开场\n发言人1 04:00:00 收尾"
        self.assertEqual(transcript_duration_seconds(text), 4 * 60 * 60)
        self.assertEqual(_target_chapter_stages(text, has_ts=True), 40)

    def test_character_proxy_matches_long_class_scale(self):
        self.assertEqual(_target_chapter_stages("中" * 63_000, has_ts=False), 35)
        self.assertEqual(_target_chapter_stages("短课", has_ts=False), 20)
        self.assertEqual(_target_chapter_stages("中" * 200_000, has_ts=False), 50)

    def test_segment_budgets_sum_to_target(self):
        budgets = _allocate_stage_budgets(["a" * 100, "b" * 300, "c" * 600], 20)
        self.assertEqual(sum(budgets), 20)
        self.assertTrue(all(budget >= 1 for budget in budgets))
        self.assertGreater(budgets[2], budgets[0])

    def test_make_chapters_finishes_near_dynamic_target(self):
        provider = FakeProvider()
        source = "课堂内容。" * 7_000  # 约 3.5 万字，目标 20 个阶段

        result = _make_chapters(provider, "fake", "", "", source, has_ts=False)

        self.assertEqual(_count_stages(result), 20)
        self.assertEqual(provider.merge_calls, 1)
        self.assertIn("20 个阶段", provider.calls[0][0])

    def test_make_chapters_retries_out_of_range_merge(self):
        provider = FakeProvider(invalid_first_merge=True)
        source = "课堂内容。" * 10_800  # 约 5.4 万字，目标 30 个阶段

        result = _make_chapters(provider, "fake", "", "", source, has_ts=False)

        self.assertEqual(_count_stages(result), 30)
        self.assertEqual(provider.merge_calls, 2)
        self.assertIn("上次汇编得到 60 个阶段", provider.calls[-1][1])

    def test_make_chapters_rejects_truncated_merge_and_uses_candidates(self):
        provider = TruncatedMergeProvider()
        source = "课堂内容。" * 10_800  # 约 5.4 万字，目标 30 个阶段

        result = _make_chapters(provider, "fake", "", "", source, has_ts=False)

        self.assertEqual(_count_stages(result), 30)
        self.assertNotIn("### 阶段20：\n", result)
        self.assertEqual(provider.merge_calls, 2)


if __name__ == "__main__":
    unittest.main()
