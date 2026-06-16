"""Provider 配置的持久化存储（运行时可增删改）。

落盘到 data/providers.json，结构：
    {"default_id": "<id>",
     "vision_id": "<id|空>", "vision_model": "<model|空>",  # 图片识别（笔记照片转录）专用，空=自动选支持视觉的供应商
     "providers": [config, ...]}
每个 config：
    {
      "id": str,              # 内部唯一标识（会话 step_models 用它引用）
      "label": str,           # 显示名
      "kind": "openai"|"anthropic",  # 调用协议
      "base_url": str,        # 端点地址（官方 Claude 可留空）
      "api_key": str,
      "models": [str, ...],   # 可选模型列表
      "default_model": str,
      "supports_vision": bool
    }

首次运行（无文件）时，从既有 .env 配置播种，保持向后兼容：
沿用 claude / anthropic_compat / deepseek / openai_compat 作为稳定 id，
这样历史会话里 step_models 引用的 provider 名仍然有效。
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import List, Optional

from config import settings

_LOCK = threading.RLock()

_FIELDS = ("label", "kind", "base_url", "api_key", "models", "default_model", "supports_vision")


def _path() -> Path:
    return settings.data_path / "providers.json"


def _seed_from_env() -> dict:
    s = settings
    providers = [
        {
            "id": "claude",
            "label": "Claude（官方）",
            "kind": "anthropic",
            "base_url": "",
            "api_key": s.anthropic_api_key or "",
            "models": ["claude-opus-4-8", "claude-sonnet-4-6"],
            "default_model": s.default_model or "claude-opus-4-8",
            "supports_vision": True,
        },
        {
            "id": "anthropic_compat",
            "label": s.anthropic_compat_label or "Anthropic 中转站",
            "kind": "anthropic",
            "base_url": s.anthropic_compat_base_url or "",
            "api_key": s.anthropic_compat_api_key or "",
            "models": ["claude-opus-4-8", "claude-sonnet-4-6"],
            "default_model": s.anthropic_compat_model or "claude-sonnet-4-6",
            "supports_vision": bool(s.anthropic_compat_vision),
        },
        {
            "id": "deepseek",
            "label": "DeepSeek",
            "kind": "openai",
            "base_url": "https://api.deepseek.com",
            "api_key": s.deepseek_api_key or "",
            "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
            "default_model": "deepseek-v4-flash",
            "supports_vision": False,
        },
        {
            "id": "openai_compat",
            "label": s.openai_compat_label or "自定义模型",
            "kind": "openai",
            "base_url": s.openai_compat_base_url or "",
            "api_key": s.openai_compat_api_key or "",
            "models": [s.openai_compat_model] if s.openai_compat_model else [],
            "default_model": s.openai_compat_model or "",
            "supports_vision": bool(s.openai_compat_vision),
        },
    ]
    return {
        "default_id": s.default_provider or "deepseek",
        # 图片识别默认留空（自动选第一个支持视觉的已配置供应商）
        "vision_id": "",
        "vision_model": "",
        "providers": providers,
    }


def _load_raw() -> dict:
    p = _path()
    if not p.exists():
        data = _seed_from_env()
        _save_raw(data)
        return data
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        data = _seed_from_env()
        _save_raw(data)
        return data


def _save_raw(data: dict) -> None:
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _normalize(cfg: dict) -> dict:
    out = {k: cfg.get(k) for k in _FIELDS}
    out["label"] = (out.get("label") or "未命名").strip()
    out["kind"] = "anthropic" if out.get("kind") == "anthropic" else "openai"
    out["base_url"] = (out.get("base_url") or "").strip().rstrip("/")
    out["api_key"] = (out.get("api_key") or "").strip()
    models = out.get("models") or []
    if isinstance(models, str):
        models = [m.strip() for m in models.splitlines() if m.strip()]
    out["models"] = [m for m in models if m]
    out["default_model"] = (out.get("default_model") or "").strip()
    if not out["default_model"] and out["models"]:
        out["default_model"] = out["models"][0]
    if out["default_model"] and out["default_model"] not in out["models"]:
        out["models"].insert(0, out["default_model"])
    out["supports_vision"] = bool(out.get("supports_vision"))
    return out


# ---------- 查询 ----------
def list_configs() -> List[dict]:
    return _load_raw()["providers"]


def get_config(pid: str) -> Optional[dict]:
    return next((c for c in list_configs() if c["id"] == pid), None)


def default_id() -> str:
    data = _load_raw()
    did = data.get("default_id")
    if did and any(c["id"] == did for c in data["providers"]):
        return did
    return data["providers"][0]["id"] if data["providers"] else ""


# ---------- 写入 ----------
def add(cfg: dict) -> dict:
    with _LOCK:
        data = _load_raw()
        new = _normalize(cfg)
        new["id"] = "p_" + uuid.uuid4().hex[:8]
        data["providers"].append(new)
        if not data.get("default_id"):
            data["default_id"] = new["id"]
        _save_raw(data)
        return new


def update(pid: str, patch: dict) -> Optional[dict]:
    with _LOCK:
        data = _load_raw()
        for c in data["providers"]:
            if c["id"] == pid:
                merged = {**c, **{k: v for k, v in patch.items() if k in _FIELDS and v is not None}}
                norm = _normalize(merged)
                norm["id"] = pid
                idx = data["providers"].index(c)
                data["providers"][idx] = norm
                _save_raw(data)
                return norm
        return None


def delete(pid: str) -> bool:
    with _LOCK:
        data = _load_raw()
        before = len(data["providers"])
        data["providers"] = [c for c in data["providers"] if c["id"] != pid]
        if len(data["providers"]) == before:
            return False
        if data.get("default_id") == pid:
            data["default_id"] = data["providers"][0]["id"] if data["providers"] else ""
        _save_raw(data)
        return True


def set_default(pid: str) -> bool:
    with _LOCK:
        data = _load_raw()
        if not any(c["id"] == pid for c in data["providers"]):
            return False
        data["default_id"] = pid
        _save_raw(data)
        return True


# ---------- 图片识别（笔记照片转录）专用模型 ----------
def get_vision() -> dict:
    """返回用户为图片识别手动指定的 (provider_id, model)；未设或已失效则为空（交由调用方自动兜底）。"""
    data = _load_raw()
    vid = data.get("vision_id") or ""
    if vid and not any(c["id"] == vid for c in data["providers"]):
        vid = ""  # 引用的供应商已被删除 → 视为未设
    return {"id": vid, "model": (data.get("vision_model") or "") if vid else ""}


def set_vision(pid: Optional[str], model: Optional[str] = None) -> dict:
    """设置图片识别专用供应商/模型。pid 传空字符串/None = 清除（恢复自动）。"""
    with _LOCK:
        data = _load_raw()
        pid = (pid or "").strip()
        if pid and not any(c["id"] == pid for c in data["providers"]):
            raise ValueError("provider 不存在")
        data["vision_id"] = pid
        data["vision_model"] = (model or "").strip() if pid else ""
        _save_raw(data)
        return get_vision()
