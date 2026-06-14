"""Anthropic (Claude) Provider —— 平台默认。支持视觉。

包含两个实现，都走 Anthropic Messages 原生格式：
- ClaudeProvider：官方 Anthropic 端点（默认）。
- AnthropicCompatProvider：第三方 Anthropic 兼容中转站（自定义 url + key + model）。
"""
from __future__ import annotations

from typing import List, Optional

from config import settings

from .base import BaseProvider, ChatResult, Message, ProviderError


class _AnthropicBase(BaseProvider):
    """共享 Anthropic Messages 调用逻辑。子类提供 _api_key() / _base_url()。"""

    def _api_key(self) -> Optional[str]:
        raise NotImplementedError

    def _base_url(self) -> Optional[str]:
        return None  # None = 用 SDK 默认（官方端点）

    def _default_headers(self) -> Optional[dict]:
        return None  # 子类可覆盖（如中转站需伪装 UA 绕过 WAF）

    def chat(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> ChatResult:
        if not self._api_key():
            raise ProviderError(f"{self.name} 未配置 API key。")
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ProviderError("未安装 anthropic SDK：pip install anthropic") from e

        kwargs = {"api_key": self._api_key()}
        if self._base_url():
            kwargs["base_url"] = self._base_url()
        if self._default_headers():
            kwargs["default_headers"] = self._default_headers()
        client = anthropic.Anthropic(**kwargs)
        # 优先用显式传入的模型，否则用本 provider 自己的默认模型
        model = model or self.default_model

        # Anthropic：system 单列，其余进 messages
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_msgs = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        try:
            resp = client.messages.create(
                model=model,
                system="\n\n".join(system_parts) or None,
                messages=chat_msgs,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name} 调用失败：{e}") from e

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }
        return ChatResult(text=text, model=model, provider=self.name, usage=usage)


class ClaudeProvider(_AnthropicBase):
    """官方 Anthropic 端点（默认）。"""

    name = "claude"
    supports_vision = True

    @property
    def default_model(self) -> str:  # type: ignore[override]
        # 全局 DEFAULT_MODEL 只对官方 claude 生效
        return settings.default_model or "claude-opus-4-8"

    def is_configured(self) -> bool:
        return bool(settings.anthropic_api_key)

    def _api_key(self) -> Optional[str]:
        return settings.anthropic_api_key

    def list_models(self) -> List[str]:
        return [
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ]


class AnthropicCompatProvider(_AnthropicBase):
    """第三方 Anthropic 兼容中转站（自定义四要素）。

    四要素来自 .env：
      ANTHROPIC_COMPAT_LABEL    显示名（Provider）
      ANTHROPIC_COMPAT_BASE_URL url（中转站地址）
      ANTHROPIC_COMPAT_API_KEY  apikey
      ANTHROPIC_COMPAT_MODEL    model
    内部注册 key 固定为 "anthropic_compat"。
    """

    name = "anthropic_compat"

    @property
    def label(self) -> str:
        return settings.anthropic_compat_label or "Anthropic 中转站"

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        return settings.anthropic_compat_vision

    @property
    def default_model(self) -> str:  # type: ignore[override]
        return settings.anthropic_compat_model or ""

    def _api_key(self) -> Optional[str]:
        return settings.anthropic_compat_api_key

    def _base_url(self) -> Optional[str]:
        return settings.anthropic_compat_base_url

    def _default_headers(self) -> Optional[dict]:
        # 部分中转站前置 Cloudflare，会拦截 SDK 默认 UA；伪装成浏览器绕过
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }

    def is_configured(self) -> bool:
        return bool(
            settings.anthropic_compat_api_key
            and settings.anthropic_compat_base_url
            and self.default_model
        )

    def list_models(self) -> List[str]:
        return [self.default_model] if self.default_model else []
