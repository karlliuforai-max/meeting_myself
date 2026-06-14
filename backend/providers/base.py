"""模型 Provider 抽象层 —— 统一接口，便于自由切换模型（默认 Claude）。

每个 Provider 实现 chat()，支持纯文本消息；支持视觉的 Provider 额外声明
supports_vision=True 并能接收图片内容块。处理流水线的每一步都可独立选用
不同 provider/model（见 modules 的步骤配置）。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ChatResult:
    text: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Provider 调用失败（缺 key、网络、API 报错等）。"""


class BaseProvider(abc.ABC):
    name: str = "base"
    supports_vision: bool = False
    default_model: str = ""

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """是否具备调用条件（如 API key 就绪）。"""

    @abc.abstractmethod
    def chat(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> ChatResult:
        """发起一次对话补全，返回文本结果。"""

    def list_models(self) -> List[str]:
        """该 provider 推荐/可选的模型列表（用于前端下拉）。"""
        return [self.default_model] if self.default_model else []
