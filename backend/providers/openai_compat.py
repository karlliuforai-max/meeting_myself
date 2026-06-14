"""OpenAI 兼容调用基类 —— Chat Completions 接口。

DeepSeek 等众多服务都兼容 OpenAI Chat Completions，这里抽一层共用。
具体 Provider 由 dynamic.py 按配置（store）动态构建；本文件只保留共享的
chat 调用逻辑（_OpenAICompatBase）。子类提供 _api_key() 与 base_url。
"""
from __future__ import annotations

from typing import List, Optional

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
        # 优先用显式传入的模型，否则用本 provider 自己的默认模型
        model = model or self.default_model
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
