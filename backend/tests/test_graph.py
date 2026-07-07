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
from storage.session_store import SessionStore  # noqa: E402


class EmptyGraphProvider:
    label = "FakeGraph"

    def is_configured(self):
        return True

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        return SimpleNamespace(
            text="```mermaid\n```",
            model=model or "fake",
            provider="fake",
            usage={},
        )


class GraphGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))
        self.meta = self.store.create("business_school", "t")
        self.store.write_artifact(self.meta.id, "笺注.md", "# 笺注\n\n内容", note="seed")
        self.orig_store = engine.session_store
        self.orig_resolve_provider = engine._resolve_provider
        engine.session_store = self.store
        engine._resolve_provider = lambda meta, step: (EmptyGraphProvider(), "fake")

    def tearDown(self):
        engine.session_store = self.orig_store
        engine._resolve_provider = self.orig_resolve_provider
        self.tmp.cleanup()

    def test_graph_generation_rejects_empty_mermaid(self):
        events = list(engine.run_one_step(self.meta.id, "graph"))

        self.assertIsNone(self.store.read_artifact(self.meta.id, "脉络.mmd"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("脉络", events[-1]["message"])
        self.assertIn("空内容", events[-1]["message"])


if __name__ == "__main__":
    unittest.main()
