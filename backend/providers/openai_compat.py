"""OpenAI 兼容调用基类 —— Chat Completions 接口。

DeepSeek 等众多服务都兼容 OpenAI Chat Completions，这里抽一层共用。
具体 Provider 由 dynamic.py 按配置（store）动态构建；本文件只保留共享的
chat 调用逻辑（_OpenAICompatBase）。子类提供 _api_key() 与 base_url。
"""
from __future__ import annotations

from typing import Callable, List, Optional, Union

from .base import PROVIDER_TIMEOUT, BaseProvider, ChatResult, Message, ProviderError


def _openai_content(m: Message) -> Union[str, list]:
    """构造单条消息的 content：无图时用纯文本字符串（行为不变），
    有图时用 OpenAI 多模态数组（text + image_url data URI）。"""
    if not getattr(m, "images", None):
        return m.content
    parts: list = []
    if m.content:
        parts.append({"type": "text", "text": m.content})
    parts.extend(
        {
            "type": "image_url",
            "image_url": {"url": f"data:{img.media_type};base64,{img.data_b64}"},
        }
        for img in m.images
    )
    return parts


class _OpenAICompatBase(BaseProvider):
    """子类需提供 _api_key() 与 base_url。"""

    base_url: str = ""

    def _api_key(self) -> Optional[str]:
        raise NotImplementedError

    def is_configured(self) -> bool:
        return bool(self._api_key() and self.base_url)

    def _client(self):
        """惰性创建并缓存 OpenAI client（SDK 线程安全）：engine 每步 resolve 一次
        provider，多个 chunk 复用同一实例，省去反复建连接的开销。"""
        cached = getattr(self, "_client_cache", None)
        if cached is not None:
            return cached
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise ProviderError("未安装 openai SDK：pip install openai") from e
        client = OpenAI(api_key=self._api_key(), base_url=self.base_url, timeout=PROVIDER_TIMEOUT)
        self._client_cache = client
        return client

    def _cap_tokens(self, max_tokens: int) -> int:
        # 供应商声明了输出上限（max_output_tokens>0）时收敛请求值，避免超模型能力被中转站拒。
        cap = getattr(self, "max_output_tokens", 0) or 0
        return min(max_tokens, cap) if cap > 0 else max_tokens

    def chat(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> ChatResult:
        if not self.is_configured():
            raise ProviderError(f"{self.name} 未配置（缺 API key 或 base_url）。")
        client = self._client()
        # 优先用显式传入的模型，否则用本 provider 自己的默认模型
        model = model or self.default_model
        max_tokens = self._cap_tokens(max_tokens)
        payload = [{"role": m.role, "content": _openai_content(m)} for m in messages]

        if on_delta is not None:
            streamed = self._chat_stream(client, model, payload, temperature, max_tokens, on_delta)
            if streamed is not None:
                return streamed
            # 首个 delta 之前就失败（部分中转站不支持 stream）→ 降级非流式重试一次。

        # 部分新模型/中转站拒绝 temperature 参数（"deprecated/unsupported"）：
        # 首次被拒时去掉该参数重试一次，并记在实例上（同一实例后续调用不再发送）。
        resp = None
        send_temp = not getattr(self, "_no_temperature", False)
        for attempt in (0, 1):
            kwargs = dict(model=model, messages=payload, max_tokens=max_tokens)
            if send_temp:
                kwargs["temperature"] = temperature
            try:
                resp = client.chat.completions.create(**kwargs)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 0 and send_temp and _temperature_rejected(e):
                    self._no_temperature = True
                    send_temp = False
                    continue
                raise ProviderError(f"{self.name} 调用失败：{e}") from e

        text = resp.choices[0].message.content or ""
        finish = _normalize_finish(getattr(resp.choices[0], "finish_reason", None))
        usage = _usage_of(getattr(resp, "usage", None))
        return ChatResult(text=text, model=model, provider=self.name, usage=usage, finish_reason=finish)

    def _chat_stream(self, client, model, payload, temperature, max_tokens, on_delta):
        """流式路径。首个 delta 到达前若抛错，返回 None 让调用方降级；否则返回 ChatResult。"""
        chunks: List[str] = []
        finish = ""
        usage: dict = {}
        got_first = False
        try:
            kwargs = dict(
                model=model, messages=payload, max_tokens=max_tokens,
                stream=True, stream_options={"include_usage": True},
            )
            if not getattr(self, "_no_temperature", False):
                kwargs["temperature"] = temperature
            stream = client.chat.completions.create(**kwargs)
            for event in stream:
                if getattr(event, "usage", None):
                    usage = _usage_of(event.usage)
                for choice in getattr(event, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    piece = getattr(delta, "content", None) if delta else None
                    if piece:
                        chunks.append(piece)
                        got_first = True
                        on_delta(piece)
                    fr = getattr(choice, "finish_reason", None)
                    if fr:
                        finish = _normalize_finish(fr)
        except Exception as e:  # noqa: BLE001
            if got_first:  # 已经流出内容 → 真错误，不再降级
                raise ProviderError(f"{self.name} 调用失败：{e}") from e
            if _temperature_rejected(e):
                # 记住拒绝原因，让紧随其后的非流式降级重试直接不带 temperature。
                self._no_temperature = True
            return None
        return ChatResult(
            text="".join(chunks), model=model, provider=self.name,
            usage=usage, finish_reason=finish or "stop",
        )


def _temperature_rejected(err: Exception) -> bool:
    """判断报错是否为「该模型不接受 temperature 参数」。

    新一代模型（及部分中转站映射）会对 temperature 返回 400 deprecated/unsupported；
    这类错误应自动去参重试，而不是让整次生成失败。"""
    s = str(err).lower()
    return "temperature" in s and any(
        k in s for k in ("deprecated", "unsupported", "not supported", "不支持")
    )


def _normalize_finish(reason: Optional[str]) -> str:
    return "length" if reason == "length" else "stop"


def _usage_of(usage) -> dict:
    if not usage:
        return {}
    return {
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
    }
