"""v0.8.0 包 A 测试：个人画像三级优先级与 API、笔记照片用户校对覆盖、
note_status 三态、失败原因透传进 info.failures、notes API 三端点。

隔离方式：tmp_path 隔离数据目录 + monkeypatch 替换模块级单例/依赖。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from providers import ProviderError  # noqa: E402
from storage import persona as persona_store  # noqa: E402
from storage.session_store import SessionStore  # noqa: E402
from pipeline import vision  # noqa: E402
import api.routes as routes  # noqa: E402
from main import app  # noqa: E402


# ---------- A1 个人画像三级优先级 ----------
def test_persona_priority_project_over_global(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    persona_store.set_global("全局画像")
    meta = SimpleNamespace(persona="  项目画像  ")
    got = persona_store.effective(meta)
    assert got == {"text": "项目画像", "source": "project"}


def test_persona_priority_global_when_project_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    persona_store.set_global("全局画像")
    meta = SimpleNamespace(persona="   ")
    assert persona_store.effective(meta) == {"text": "全局画像", "source": "global"}


def test_persona_priority_default_when_all_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    # 未设置全局、项目为空 → 系统默认
    meta = SimpleNamespace(persona="")
    got = persona_store.effective(meta)
    assert got == {"text": persona_store.DEFAULT_PERSONA, "source": "default"}


def test_persona_effective_old_meta_without_attr(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    persona_store.set_global("全局画像")
    # 老对象没有 persona 字段：getattr 容错，回退全局
    old = SimpleNamespace()
    assert persona_store.effective(old)["source"] == "global"


def test_set_global_empty_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    assert persona_store.set_global("  我的画像 ") == "我的画像"
    assert persona_store.get_global() == "我的画像"
    # 空串=清除，恢复系统默认
    assert persona_store.set_global("") == ""
    assert persona_store.get_global() == ""
    assert not (tmp_path / "persona.txt").exists()


# ---------- A2 画像 API ----------
def test_persona_api_get_put(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    client = TestClient(app)
    r = client.get("/api/persona")
    assert r.status_code == 200
    assert r.json() == {"text": "", "default": persona_store.DEFAULT_PERSONA}

    r = client.put("/api/persona", json={"text": "消费投研分析师"})
    assert r.status_code == 200
    assert r.json()["text"] == "消费投研分析师"
    assert persona_store.get_global() == "消费投研分析师"

    # 空串清除
    r = client.put("/api/persona", json={"text": ""})
    assert r.json()["text"] == ""


# ---------- A1 SessionMeta.persona 前向兼容 ----------
def test_session_meta_persona_forward_compat(tmp_path):
    store = SessionStore(root=tmp_path)
    meta = store.create("business_school", "t")
    assert meta.persona == ""  # 新字段默认空
    # 老 meta.json（无 persona 键）读取不崩、默认空
    p = store._meta_path(meta.id)
    import json
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.pop("persona", None)
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    got = store.get(meta.id)
    assert got is not None and got.persona == ""


# ---------- A5 用户校对覆盖 ----------
def test_user_note_overrides_machine_cache(tmp_path):
    store = SessionStore(root=tmp_path)
    meta = store.create("business_school", "t")
    p = store.save_input(meta.id, "n.png", b"img-v1")
    store.write_note_cache(meta.id, "n.png", "机器转录")
    assert store.read_note_cache(meta.id, "n.png") == "机器转录"
    # 用户校对覆盖优先
    assert store.write_user_note(meta.id, "n.png", "人工校对") is True
    assert store.read_note_cache(meta.id, "n.png") == "人工校对"
    # 图片改动（mtime 变）→ 机器缓存失效，但用户校对不失效
    import os, time
    future = time.time() + 5
    os.utime(p, (future, future))
    assert store.read_note_cache(meta.id, "n.png") == "人工校对"
    # 空串删除覆盖 → 恢复机器缓存（此处机器缓存已因 mtime 失效 → None）
    assert store.write_user_note(meta.id, "n.png", "") is True
    assert store.read_note_cache(meta.id, "n.png") is None


def test_write_user_note_rejects_missing_file(tmp_path):
    store = SessionStore(root=tmp_path)
    meta = store.create("business_school", "t")
    assert store.write_user_note(meta.id, "missing.png", "x") is False


def test_note_status_three_states(tmp_path):
    store = SessionStore(root=tmp_path)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "n.png", b"img")
    assert store.note_status(meta.id, "n.png")["status"] == "none"
    store.write_note_cache(meta.id, "n.png", "机器")
    st = store.note_status(meta.id, "n.png")
    assert st["status"] == "cached" and st["text"] == "机器"
    store.write_user_note(meta.id, "n.png", "人工")
    st = store.note_status(meta.id, "n.png")
    assert st["status"] == "user" and st["text"] == "人工"


# ---------- A4 失败原因透传 ----------
class _RaisingProvider:
    label = "Raiser"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        self.calls += 1
        raise ProviderError("视觉服务限流 429")


def test_collect_note_text_failures_in_info(tmp_path, monkeypatch):
    store = SessionStore(root=tmp_path)
    monkeypatch.setattr(vision, "session_store", store)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "bad.png", b"img")
    fake = _RaisingProvider()
    monkeypatch.setattr(vision, "resolve_vision_provider", lambda: (fake, "m"))
    text, info = vision.collect_note_text(meta.id)
    assert text == ""
    assert info["failed"] == 1
    assert "bad.png" in info["failures"]
    assert "限流" in info["failures"]["bad.png"]
    # 失败自动重试一次：共两次尝试
    assert fake.calls == 2


# ---------- A6 notes API ----------
class _OkProvider:
    label = "OkVision"

    def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        return SimpleNamespace(text="机器识别结果", model=model or "m", provider="ok", usage={})


def _client_with_store(tmp_path, monkeypatch):
    store = SessionStore(root=tmp_path)
    monkeypatch.setattr(routes, "session_store", store)
    return TestClient(app), store


def test_notes_api_list_and_edit(tmp_path, monkeypatch):
    client, store = _client_with_store(tmp_path, monkeypatch)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "n.png", b"img")

    r = client.get(f"/api/sessions/{meta.id}/notes")
    assert r.status_code == 200
    notes = r.json()["notes"]
    assert len(notes) == 1 and notes[0]["status"] == "none"

    r = client.put(f"/api/sessions/{meta.id}/notes/n.png", json={"text": "人工校对"})
    assert r.status_code == 200
    assert r.json() == {"filename": "n.png", "status": "user", "text": "人工校对"}

    # 空串删除覆盖 → 回到 none（无机器缓存）
    r = client.put(f"/api/sessions/{meta.id}/notes/n.png", json={"text": ""})
    assert r.json()["status"] == "none"


def test_notes_api_edit_404_for_nonimage(tmp_path, monkeypatch):
    client, store = _client_with_store(tmp_path, monkeypatch)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "doc.txt", b"x")
    r = client.put(f"/api/sessions/{meta.id}/notes/doc.txt", json={"text": "x"})
    assert r.status_code == 404
    r = client.put(f"/api/sessions/{meta.id}/notes/missing.png", json={"text": "x"})
    assert r.status_code == 404


def test_notes_api_transcribe_success(tmp_path, monkeypatch):
    client, store = _client_with_store(tmp_path, monkeypatch)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "n.png", b"img")
    monkeypatch.setattr(vision, "resolve_vision_provider", lambda: (_OkProvider(), "m"))
    r = client.post(f"/api/sessions/{meta.id}/notes/n.png/transcribe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["machine_text"] == "机器识别结果"
    assert body["status"] == "cached"


def test_notes_api_transcribe_no_model(tmp_path, monkeypatch):
    client, store = _client_with_store(tmp_path, monkeypatch)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "n.png", b"img")
    monkeypatch.setattr(vision, "resolve_vision_provider", lambda: (None, None))
    r = client.post(f"/api/sessions/{meta.id}/notes/n.png/transcribe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "模型配置" in body["error"]


def test_notes_api_transcribe_keeps_user_override(tmp_path, monkeypatch):
    """有用户校对时强制机器转录：note_status 仍报 user，但 machine_text 给出新机器结果。"""
    client, store = _client_with_store(tmp_path, monkeypatch)
    meta = store.create("business_school", "t")
    store.save_input(meta.id, "n.png", b"img")
    store.write_user_note(meta.id, "n.png", "我的校对")
    monkeypatch.setattr(vision, "resolve_vision_provider", lambda: (_OkProvider(), "m"))
    r = client.post(f"/api/sessions/{meta.id}/notes/n.png/transcribe")
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "user"
    assert body["text"] == "我的校对"
    assert body["machine_text"] == "机器识别结果"
