"""按配置动态构建 Provider —— 复用既有的两套 chat 调用逻辑。

- kind="anthropic" → 走 Anthropic Messages 格式（_AnthropicBase）
- kind="openai"    → 走 OpenAI Chat Completions 格式（_OpenAICompatBase）

配置来自 store（data/providers.json），instance 级覆盖各项，
不再依赖全局 .env settings。
"""
from __future__ import annotations

from typing import List, Optional

from .base import BaseProvider
from .claude import _AnthropicBase
from .openai_compat import _OpenAICompatBase

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


class _ConfigMixin:
    """从 config dict 取各项，覆盖 BaseProvider 的类属性/方法。"""

    def __init__(self, cfg: dict):
        self._cfg = dict(cfg)
        self.name = cfg["id"]

    @property
    def label(self) -> str:
        return self._cfg.get("label") or self.name

    @property
    def supports_vision(self) -> bool:
        return bool(self._cfg.get("supports_vision"))

    @property
    def default_model(self) -> str:
        return self._cfg.get("default_model") or ""

    def list_models(self) -> List[str]:
        ms = list(self._cfg.get("models") or [])
        dm = self.default_model
        if dm and dm not in ms:
            ms.insert(0, dm)
        return ms

    def _api_key(self) -> Optional[str]:
        return self._cfg.get("api_key") or None


class DynamicAnthropicProvider(_ConfigMixin, _AnthropicBase):
    def _base_url(self) -> Optional[str]:
        return self._cfg.get("base_url") or None  # 空 = SDK 默认（官方端点）

    def _default_headers(self) -> Optional[dict]:
        # 仅第三方中转站（配了自定义 base_url）伪装浏览器 UA 绕过 Cloudflare
        if self._cfg.get("base_url"):
            return {"User-Agent": _BROWSER_UA}
        return None

    def is_configured(self) -> bool:
        return bool(self._api_key() and self.default_model)


class DynamicOpenAIProvider(_ConfigMixin, _OpenAICompatBase):
    @property
    def base_url(self) -> str:  # type: ignore[override]
        return self._cfg.get("base_url") or ""

    def is_configured(self) -> bool:
        return bool(self._api_key() and self.base_url and self.default_model)


def build_provider(cfg: dict) -> BaseProvider:
    if cfg.get("kind") == "anthropic":
        return DynamicAnthropicProvider(cfg)
    return DynamicOpenAIProvider(cfg)
