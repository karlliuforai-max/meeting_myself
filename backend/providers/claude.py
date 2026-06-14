"""Claude (Anthropic) Provider —— 平台默认。支持视觉。"""
from __future__ import annotations

from typing import List, Optional

from config import settings

from .base import BaseProvider, ChatResult, Message, ProviderError


class ClaudeProvider(BaseProvider):
    name = "claude"
    supports_vision = True
    default_model = "claude-opus-4-8"

    def is_configured(self) -> bool:
        return bool(settings.anthropic_api_key)

    def list_models(self) -> List[str]:
        return [
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ]

    def chat(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> ChatResult:
        if not self.is_configured():
            raise ProviderError("缺少 ANTHROPIC_API_KEY，无法调用 Claude。")
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ProviderError("未安装 anthropic SDK：pip install anthropic") from e

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        model = model or settings.default_model or self.default_model

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
            raise ProviderError(f"Claude 调用失败：{e}") from e

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
