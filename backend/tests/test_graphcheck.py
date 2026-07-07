"""脉络连通性：graphcheck 解析/判定单元测试 + engine 返修与拒写路径。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pipeline.engine as engine  # noqa: E402
from pipeline import graphcheck  # noqa: E402
from storage.session_store import SessionStore  # noqa: E402


CONNECTED = """flowchart LR
    ROOT["课程主题"]
    subgraph SG1["分支一"]
        A1["a"]
        A2["b"]
    end
    subgraph SG2["分支二"]
        B1["c"]
    end
    ROOT -->|展开| A1
    ROOT -->|展开| B1
    A1 -->|支撑| A2
    A2 -->|递进| B1
"""

ISLANDS = """flowchart LR
    ROOT["课程主题"]
    subgraph SG1["分支一"]
        A1["a"]
        A2["b"]
    end
    subgraph SG2["分支二"]
        B1["c"]
        B2["d"]
    end
    A1 -->|支撑| A2
    B1 -->|因果| B2
"""


class GraphcheckParseTests(unittest.TestCase):
    def test_connected_graph_ok(self):
        conn = graphcheck.connectivity(CONNECTED)
        self.assertTrue(conn["ok"])
        self.assertEqual(conn["components"], 1)
        self.assertEqual(conn["isolated_nodes"], [])

    def test_islands_detected(self):
        # ROOT 无任何连线 + 两个分支互不相连 → 三个连通分量
        conn = graphcheck.connectivity(ISLANDS)
        self.assertFalse(conn["ok"])
        self.assertEqual(conn["components"], 3)
        # 主体为最大分量，其余节点判为孤立；ROOT 一定在孤立清单里
        self.assertIn("ROOT", conn["isolated_nodes"])

    def test_chain_edges_parsed(self):
        nodes, edges = graphcheck.parse_mermaid(
            'flowchart LR\n    A["x"] -->|支撑| B --> C{"y"}\n'
        )
        self.assertEqual(nodes, {"A", "B", "C"})
        self.assertIn(("A", "B"), edges)
        self.assertIn(("B", "C"), edges)

    def test_quoted_text_not_misparsed(self):
        # 引号里的 --> 与英文词不能被当成结构；只应识别 A、B 两个节点一条边
        nodes, edges = graphcheck.parse_mermaid(
            'flowchart LR\n    A["WACC --> ROE 之争"] -->|对比| B["exit --> entry"]\n'
        )
        self.assertEqual(nodes, {"A", "B"})
        self.assertEqual(edges, [("A", "B")])

    def test_empty_or_edgeless_rejected(self):
        self.assertFalse(graphcheck.connectivity("")["ok"])
        self.assertFalse(graphcheck.connectivity('flowchart LR\n    A["孤零零"]\n')["ok"])

    def test_subgraph_id_not_counted_unless_linked(self):
        nodes, _ = graphcheck.parse_mermaid(CONNECTED)
        # subgraph 声明行的 SG1/SG2 不算内容节点（未出现在任何边里）
        self.assertNotIn("SG1", nodes)
        self.assertNotIn("SG2", nodes)


class _RepairableProvider:
    """首次返回孤岛图，返修后返回连通图：驱动 engine 的连通性返修路径。"""

    label = "FakeGraph"

    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        self.calls += 1
        code = ISLANDS if self.calls == 1 else CONNECTED
        return SimpleNamespace(
            text=f"```mermaid\n{code}```",
            model=model or "fake", provider="fake", usage={}, finish_reason="stop",
        )


class _AlwaysIslandProvider(_RepairableProvider):
    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        self.calls += 1
        return SimpleNamespace(
            text=f"```mermaid\n{ISLANDS}```",
            model=model or "fake", provider="fake", usage={}, finish_reason="stop",
        )


class GraphEngineConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))
        self.meta = self.store.create("business_school", "t")
        self.store.write_artifact(self.meta.id, "笺注.md", "# 笺注\n\n内容", note="seed")
        self.orig_store = engine.session_store
        self.orig_resolve_provider = engine._resolve_provider
        engine.session_store = self.store

    def tearDown(self):
        engine.session_store = self.orig_store
        engine._resolve_provider = self.orig_resolve_provider
        self.tmp.cleanup()

    def _run(self, provider):
        engine._resolve_provider = lambda meta, step: (provider, "fake")
        return list(engine.run_one_step(self.meta.id, "graph"))

    def test_island_graph_repaired_once_then_written(self):
        provider = _RepairableProvider()
        events = self._run(provider)

        self.assertEqual(provider.calls, 2)  # 首次孤岛 → 带反馈重试一次
        self.assertEqual(events[-1]["type"], "done")
        code = self.store.read_artifact(self.meta.id, "脉络.mmd")
        self.assertIsNotNone(code)
        self.assertTrue(graphcheck.connectivity(code)["ok"])

    def test_island_graph_twice_rejected_not_written(self):
        provider = _AlwaysIslandProvider()
        events = self._run(provider)

        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("连通", events[-1]["message"])
        self.assertIsNone(self.store.read_artifact(self.meta.id, "脉络.mmd"))

    def test_revise_graph_rejects_disconnected(self):
        # 先放一张合法旧图，修订返回孤岛图 → 报错且旧图不被覆盖
        self.store.write_artifact(self.meta.id, "脉络.mmd", CONNECTED, note="seed")
        provider = _AlwaysIslandProvider()
        engine._resolve_provider = lambda meta, step: (provider, "fake")
        events = list(engine.revise_one_step(self.meta.id, "graph", "改一下"))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.store.read_artifact(self.meta.id, "脉络.mmd"), CONNECTED)


if __name__ == "__main__":
    unittest.main()
