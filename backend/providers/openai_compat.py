"""OpenAI 兼容 Provider 基类 + 通用兜底实现。

DeepSeek 等众多服务都兼容 OpenAI Chat Completions 接口，这里抽一层共用。
通用兜底（OpenAICompatProvider）允许用户填任意 base_url + key 接入自定义端点。
"""
from __future__ import annotations

from typing import List, Optional

from config import settings

from .base import BaseProvider, ChatResult, Message, ProviderError


class _OpenAICompatBase(BaseProvider):
    """子类需提供 _api_key() 与 base_url。"""

    base_url: str = ""

    def _api_key(self) -> Optional[str]:
        raise NotImplementedError

    def is_configured(self) -> bool:
        return bool(self._api_key() and self.base_url)

    def chat(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> ChatResult:
        if not self.is_configured():
            raise ProviderError(f"{self.name} 未配置（缺 API key 或 base_url）。")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise ProviderError("未安装 openai SDK：pip install openai") from e

        client = OpenAI(api_key=self._api_key(), base_url=self.base_url)
        model = model or settings.default_model or self.default_model
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name} 调用失败：{e}") from e

        text = resp.choices[0].message.content or ""
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
            }
        return ChatResult(text=text, model=model, provider=self.name, usage=usage)


class DeepSeekProvider(_OpenAICompatBase):
    name = "deepseek"
    supports_vision = False
    default_model = "deepseek-chat"
    base_url = "https://api.deepseek.com"

    def _api_key(self) -> Optional[str]:
        return settings.deepseek_api_key

    def list_models(self) -> List[str]:
        return ["deepseek-chat", "deepseek-reasoner"]


class OpenAICompatProvider(_OpenAICompatBase):
    """通用兜底：接入任意 OpenAI 兼容端点。"""

    name = "openai_compat"
    supports_vision = False
    default_model = "gpt-4o-mini"

    @property
    def base_url(self) -> str:  # type: ignore[override]
        return settings.openai_compat_base_url or ""

    def _api_key(self) -> Optional[str]:
        return settings.openai_compat_api_key
