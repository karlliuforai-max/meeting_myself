"""Provider 注册表：按名取 Provider，列出可用模型，按步骤选用。"""
from __future__ import annotations

from typing import Dict, List, Optional

from config import settings

from .base import BaseProvider, ProviderError
from .claude import AnthropicCompatProvider, ClaudeProvider
from .openai_compat import DeepSeekProvider, OpenAICompatProvider

_PROVIDERS: Dict[str, BaseProvider] = {
    "claude": ClaudeProvider(),
    "anthropic_compat": AnthropicCompatProvider(),
    "deepseek": DeepSeekProvider(),
    "openai_compat": OpenAICompatProvider(),
}


def get_provider(name: Optional[str] = None) -> BaseProvider:
    name = name or settings.default_provider or "claude"
    if name not in _PROVIDERS:
        raise ProviderError(f"未知 provider：{name}（可选：{', '.join(_PROVIDERS)}）")
    return _PROVIDERS[name]


def list_providers() -> List[dict]:
    """供前端"设置/切换模型"使用：每个 provider 的可用性与模型列表。"""
    out = []
    for name, p in _PROVIDERS.items():
        out.append(
            {
                "name": name,
                "label": getattr(p, "label", name),
                "configured": p.is_configured(),
                "supports_vision": p.supports_vision,
                "default_model": p.default_model,
                "models": p.list_models(),
                "is_default": name == (settings.default_provider or "claude"),
            }
        )
    return out
