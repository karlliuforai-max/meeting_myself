"""P? 加固项测试：路径穿越、step 白名单、截断检测、坏配置保留、meta 前向兼容、
纲目下限、编码探测、输出上限收敛、API key 不回传明文。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api.routes as routes  # noqa: E402
import pipeline.engine as engine  # noqa: E402
from main import app  # noqa: E402
from providers import store as provider_store  # noqa: E402
from providers.base import ChatResult  # noqa: E402
from storage.session_store import SessionStore, _decode_text  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ---------- S1 上传路径穿越 ----------
class PathTraversalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))
        self.meta = self.store.create("business_school", "t")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_traversal_names(self):
        # 净化后 basename 为空 / "." / ".." 一律拒绝（含 "../"、"..\\"、纯 ".."）
        for bad in ("../", "..\\", "..", "../..", ".", ""):
            with self.assertRaises(ValueError):
                self.store.save_input(self.meta.id, bad, b"x")

    def test_sanitizes_directory_prefix(self):
        # 带目录/穿越前缀但有真实文件名：取 basename 落到 inputs/ 内，绝不写到目录之外
        for name in ("sub/dir/note.txt", "../evil.txt", "..\\evil.txt"):
            p = self.store.save_input(self.meta.id, name, b"x")
            self.assertEqual(p.parent.name, "inputs")
        outside = Path(self.tmp.name).parent
        self.assertFalse((outside / "evil.txt").exists())

    def test_delete_rejects_backslash(self):
        self.assertFalse(self.store.delete_input(self.meta.id, "..\\x"))


# ---------- S2 step 白名单 ----------
class StepWhitelistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))
        self.meta = self.store.create("business_school", "t")
        self.orig = routes.session_store
        routes.session_store = self.store
        self.client = TestClient(app)

    def tearDown(self):
        routes.session_store = self.orig
        self.tmp.cleanup()

    def test_run_step_bad_step_400(self):
        r = self.client.post(f"/api/sessions/{self.meta.id}/run-step", params={"step": "../etc"})
        self.assertEqual(r.status_code, 400)

    def test_stream_bad_step_400(self):
        r = self.client.get(f"/api/sessions/{self.meta.id}/run-step-stream", params={"step": "evil"})
        self.assertEqual(r.status_code, 400)

    def test_revise_bad_step_400(self):
        r = self.client.post(f"/api/sessions/{self.meta.id}/revise-step",
                             params={"step": "boom"}, json={"instruction": "x"})
        self.assertEqual(r.status_code, 400)


# ---------- S3 截断检测 ----------
class FinishReasonProvider:
    def __init__(self, finish):
        self.finish = finish

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096, on_delta=None):
        return ChatResult(text="部分内容", model=model or "m", provider="p",
                          usage={}, finish_reason=self.finish)


class TruncationTests(unittest.TestCase):
    def test_length_raises(self):
        from providers import ProviderError
        with self.assertRaises(ProviderError):
            engine._call(FinishReasonProvider("length"), "m", "s", "u",
                         temperature=0.2, max_tokens=10)

    def test_stop_ok(self):
        txt = engine._call(FinishReasonProvider("stop"), "m", "s", "u",
                           temperature=0.2, max_tokens=10)
        self.assertEqual(txt, "部分内容")

    def test_allow_truncation_returns_text(self):
        txt = engine._call(FinishReasonProvider("length"), "m", "s", "u",
                           temperature=0.2, max_tokens=10, allow_truncation=True)
        self.assertEqual(txt, "部分内容")

    def test_openai_normalize(self):
        from providers.openai_compat import _normalize_finish
        self.assertEqual(_normalize_finish("length"), "length")
        self.assertEqual(_normalize_finish("stop"), "stop")
        self.assertEqual(_normalize_finish(None), "stop")

    def test_claude_normalize(self):
        from providers.claude import _normalize_finish
        self.assertEqual(_normalize_finish("max_tokens"), "length")
        self.assertEqual(_normalize_finish("end_turn"), "stop")


# ---------- S5 坏 providers.json 保留 ----------
class CorruptProvidersTests(unittest.TestCase):
    def test_corrupt_file_renamed_not_overwritten(self):
        with TemporaryDirectory() as d:
            data_dir = Path(d)
            orig = provider_store.settings.data_dir
            provider_store.settings.data_dir = str(data_dir)
            try:
                p = data_dir / "providers.json"
                p.write_text("{ this is not json", encoding="utf-8")
                data = provider_store._load_raw()  # 触发解析失败
                self.assertIn("providers", data)  # 已重新播种
                backups = list(data_dir.glob("providers.json.corrupt-*"))
                self.assertEqual(len(backups), 1)
                self.assertIn("this is not json", backups[0].read_text(encoding="utf-8"))
            finally:
                provider_store.settings.data_dir = orig


# ---------- S6 SessionMeta 前向兼容 ----------
class MetaCompatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_field_ignored(self):
        meta = self.store.create("business_school", "t")
        p = self.store._meta_path(meta.id)
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["future_field"] = {"whatever": 1}
        p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        got = self.store.get(meta.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.title, "t")

    def test_broken_json_returns_none(self):
        meta = self.store.create("business_school", "t")
        self.store._meta_path(meta.id).write_text("{bad", encoding="utf-8")
        self.assertIsNone(self.store.get(meta.id))


# ---------- S10 短课下限 ----------
class ChapterFloorTests(unittest.TestCase):
    def test_short_class_not_floored_to_20(self):
        self.assertEqual(engine.CHAPTERS_MIN_STAGES, 4)
        self.assertEqual(engine._target_chapter_stages("短课", has_ts=False), 4)
        # 30 分钟课（有时长）→ 30/6 = 5，不再被抬到 20
        self.assertEqual(engine._target_chapter_stages("", has_ts=True, duration=30 * 60), 5)


# ---------- S11 编码探测 ----------
class DecodeTests(unittest.TestCase):
    def test_gb18030_bytes(self):
        raw = "第一章 课堂纪要".encode("gb18030")
        self.assertEqual(_decode_text(raw), "第一章 课堂纪要")

    def test_utf8_bom(self):
        raw = "内容".encode("utf-8-sig")
        self.assertEqual(_decode_text(raw), "内容")

    def test_read_parts_multi_file(self):
        with TemporaryDirectory() as d:
            store = SessionStore(root=Path(d))
            meta = store.create("business_school", "t")
            store.save_input(meta.id, "a.txt", "甲".encode("gb18030"))
            store.save_input(meta.id, "b.txt", "乙".encode("utf-8"))
            parts = store.read_text_input_parts(meta.id)
            self.assertEqual(parts, ["甲", "乙"])


# ---------- S13 输出上限收敛 ----------
class MaxOutputClampTests(unittest.TestCase):
    def test_cap_applies(self):
        from providers.dynamic import build_provider
        cfg = {"id": "x", "kind": "openai", "base_url": "http://h", "api_key": "k",
               "models": ["m"], "default_model": "m", "max_output_tokens": 500}
        prov = build_provider(cfg)
        self.assertEqual(prov.max_output_tokens, 500)
        self.assertEqual(prov._cap_tokens(4096), 500)
        self.assertEqual(prov._cap_tokens(100), 100)

    def test_zero_means_unlimited(self):
        from providers.dynamic import build_provider
        cfg = {"id": "x", "kind": "openai", "base_url": "http://h", "api_key": "k",
               "models": ["m"], "default_model": "m", "max_output_tokens": 0}
        prov = build_provider(cfg)
        self.assertEqual(prov._cap_tokens(9999), 9999)

    def test_normalize_illegal(self):
        norm = provider_store._normalize({"label": "x", "max_output_tokens": "abc"})
        self.assertEqual(norm["max_output_tokens"], 0)


# ---------- S16 API key 不回传明文 ----------
class ApiKeyMaskingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.orig_data_dir = provider_store.settings.data_dir
        provider_store.settings.data_dir = str(self.data_dir)
        self.client = TestClient(app)

    def tearDown(self):
        provider_store.settings.data_dir = self.orig_data_dir
        self.tmp.cleanup()

    def test_detail_hides_plaintext_key(self):
        created = provider_store.add({
            "label": "秘密", "kind": "openai", "base_url": "http://h",
            "api_key": "sk-1234567890abcdef", "models": ["m"], "default_model": "m",
        })
        r = self.client.get(f"/api/providers/{created['id']}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["api_key"], "")
        self.assertTrue(body["has_key"])
        self.assertEqual(body["api_key_masked"], "sk-1…cdef")
        self.assertNotIn("sk-1234567890abcdef", r.text)


if __name__ == "__main__":
    unittest.main()


# ---------- temperature 被模型拒绝时自动去参重试（v0.8.0）----------
class _TempRejectingClient:
    """假 OpenAI client：带 temperature 的请求 400，去掉后成功。"""

    def __init__(self):
        self.calls = []

        class _Completions:
            def __init__(self, outer):
                self.outer = outer

            def create(self, **kwargs):
                self.outer.calls.append(dict(kwargs))
                if "temperature" in kwargs:
                    raise RuntimeError("400 - `temperature` is deprecated for this model.")
                from types import SimpleNamespace
                choice = SimpleNamespace(
                    message=SimpleNamespace(content="ok"), finish_reason="stop")
                return SimpleNamespace(choices=[choice], usage=None)

        class _Chat:
            def __init__(self, outer):
                self.completions = _Completions(outer)

        self.chat = _Chat(self)


def test_openai_temperature_rejection_retries_without_param():
    from providers.dynamic import DynamicOpenAIProvider
    from providers.base import Message

    p = DynamicOpenAIProvider({
        "id": "t", "label": "t", "kind": "openai",
        "base_url": "http://x", "api_key": "k",
        "models": ["m"], "default_model": "m", "supports_vision": False,
    })
    fake = _TempRejectingClient()
    p._client_cache = fake

    res = p.chat([Message("user", "hi")], model="m", temperature=0.3, max_tokens=16)
    assert res.text == "ok"
    # 第一次带 temperature 被拒，第二次去参成功
    assert "temperature" in fake.calls[0] and "temperature" not in fake.calls[1]

    # 实例已记住：后续调用直接不带 temperature（只多一次调用）
    n = len(fake.calls)
    p.chat([Message("user", "hi2")], model="m", temperature=0.3, max_tokens=16)
    assert len(fake.calls) == n + 1 and "temperature" not in fake.calls[-1]
