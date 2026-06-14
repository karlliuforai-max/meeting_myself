"""Provider 注册表：从持久化 store 动态构建，按名取 Provider、列出可用模型。

配置在运行时可增删改（见 store.py + /api/providers CRUD），不再写死。
"""
from __future__ import annotations

from typing import List, Optional

from . import store
from .base import BaseProvider, ProviderError
from .dynamic import build_provider


def get_provider(name: Optional[str] = None) -> BaseProvider:
    pid = name or store.default_id()
    cfg = store.get_config(pid)
    if not cfg:
        avail = ", ".join(c["id"] for c in store.list_configs()) or "（空）"
        raise ProviderError(f"未知 provider：{pid}（可选：{avail}）")
    return build_provider(cfg)


def list_providers() -> List[dict]:
    """供前端「设置/切换模型」使用：每个 provider 的可用性与模型列表。
    注意：不外泄 api_key，仅给 has_key 标记；完整 key 走 get_config 详情接口。
    """
    did = store.default_id()
    out = []
    for cfg in store.list_configs():
        p = build_provider(cfg)
        out.append(
            {
                "name": cfg["id"],
                "label": p.label,
                "kind": cfg.get("kind", "openai"),
                "base_url": cfg.get("base_url", ""),
                "has_key": bool(cfg.get("api_key")),
                "configured": p.is_configured(),
                "supports_vision": p.supports_vision,
                "default_model": p.default_model,
                "models": p.list_models(),
                "is_default": cfg["id"] == did,
            }
        )
    return out
