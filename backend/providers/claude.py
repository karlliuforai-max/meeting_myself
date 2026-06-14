"""Anthropic (Claude) 调用基类 —— Anthropic Messages 原生格式。

具体 Provider 由 dynamic.py 按配置（store）动态构建；本文件只保留共享的
chat 调用逻辑（_AnthropicBase）。子类提供 _api_key() / _base_url() /
_default_headers()。同时支持官方端点与第三方中转站。
"""
from __future__ import annotations

from typing import List, Optional

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
