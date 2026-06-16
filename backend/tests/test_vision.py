from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from providers.base import ImagePart, Message  # noqa: E402
from providers.claude import _anthropic_content  # noqa: E402
from providers.openai_compat import _openai_content  # noqa: E402
from storage.session_store import SessionStore  # noqa: E402
from pipeline import vision  # noqa: E402


class ContentBuilderTests(unittest.TestCase):
    def test_plain_text_unchanged(self):
        m = Message("user", "你好")
        self.assertEqual(_anthropic_content(m), "你好")
        self.assertEqual(_openai_content(m), "你好")

    def test_anthropic_image_blocks(self):
        m = Message("user", "转录", images=[ImagePart("image/png", "QUJD")])
        content = _anthropic_content(m)
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertEqual(content[0]["source"]["data"], "QUJD")
        # 文本块在图片之后
        self.assertEqual(content[-1], {"type": "text", "text": "转录"})

    def test_openai_image_url(self):
        m = Message("user", "转录", images=[ImagePart("image/jpeg", "QUJD")])
        content = _openai_content(m)
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "转录"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64,QUJD")


class NoteCacheTests(unittest.TestCase):
    def test_list_images_and_media_type(self):
        with TemporaryDirectory() as d:
            store = SessionStore(root=Path(d))
            meta = store.create("business_school", "t")
            store.save_input(meta.id, "稿子.txt", b"hello")
            store.save_input(meta.id, "笔记1.JPG", b"\xff\xd8\xff")
            store.save_input(meta.id, "板书.webp", b"RIFF")
            self.assertEqual(store.list_image_inputs(meta.id), ["板书.webp", "笔记1.JPG"])
            self.assertEqual(store.image_media_type("笔记1.JPG"), "image/jpeg")
            self.assertEqual(store.image_media_type("板书.webp"), "image/webp")

    def test_note_cache_invalidates_on_mtime(self):
        with TemporaryDirectory() as d:
            store = SessionStore(root=Path(d))
            meta = store.create("business_school", "t")
            p = store.save_input(meta.id, "n.png", b"img-v1")
            self.assertIsNone(store.read_note_cache(meta.id, "n.png"))
            store.write_note_cache(meta.id, "n.png", "转录v1")
            self.assertEqual(store.read_note_cache(meta.id, "n.png"), "转录v1")
            # 重新上传（mtime 变化）→ 缓存失效
            import os
            future = time.time() + 5
            os.utime(p, (future, future))
            self.assertIsNone(store.read_note_cache(meta.id, "n.png"))


class FakeVisionProvider:
    label = "FakeVision"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        self.calls += 1
        # 用图片字节数模拟不同转录结果
        n_imgs = len(getattr(messages[-1], "images", []))
        return SimpleNamespace(text=f"转录内容#{self.calls}（{n_imgs}图）", model=model or "fv",
                               provider="fv", usage={})


class CollectNoteTextTests(unittest.TestCase):
    def _patch_store(self, store):
        self._orig = vision.session_store
        vision.session_store = store

    def tearDown(self):
        if hasattr(self, "_orig"):
            vision.session_store = self._orig

    def test_no_images_returns_empty(self):
        with TemporaryDirectory() as d:
            store = SessionStore(root=Path(d))
            self._patch_store(store)
            meta = store.create("business_school", "t")
            store.save_input(meta.id, "a.txt", b"x")
            text, info = vision.collect_note_text(meta.id)
            self.assertEqual(text, "")
            self.assertEqual(info["images"], 0)

    def test_no_vision_provider_warns(self):
        with TemporaryDirectory() as d:
            store = SessionStore(root=Path(d))
            self._patch_store(store)
            meta = store.create("business_school", "t")
            store.save_input(meta.id, "n.png", b"img")
            orig = vision.resolve_vision_provider
            vision.resolve_vision_provider = lambda: (None, None)
            try:
                text, info = vision.collect_note_text(meta.id)
            finally:
                vision.resolve_vision_provider = orig
            self.assertEqual(text, "")
            self.assertEqual(info["images"], 1)
            self.assertFalse(info["vision"])

    def test_transcribes_and_caches(self):
        with TemporaryDirectory() as d:
            store = SessionStore(root=Path(d))
            self._patch_store(store)
            meta = store.create("business_school", "t")
            store.save_input(meta.id, "n1.png", b"imgA")
            store.save_input(meta.id, "n2.png", b"imgB")
            fake = FakeVisionProvider()
            orig = vision.resolve_vision_provider
            vision.resolve_vision_provider = lambda: (fake, "m")
            try:
                text, info = vision.collect_note_text(meta.id)
                self.assertIn("### 笔记照片：n1.png", text)
                self.assertIn("### 笔记照片：n2.png", text)
                self.assertEqual(info["transcribed"], 2)
                self.assertEqual(fake.calls, 2)
                # 第二次：全部命中缓存，不再调用模型
                text2, info2 = vision.collect_note_text(meta.id)
                self.assertEqual(fake.calls, 2)
                self.assertEqual(info2["cached"], 2)
                self.assertEqual(info2["transcribed"], 0)
            finally:
                vision.resolve_vision_provider = orig


if __name__ == "__main__":
    unittest.main()
