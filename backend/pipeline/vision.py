"""图片视觉处理：把课堂笔记照片等图片素材转录为 Markdown 文本。

设计（见开发文档 P3）：
  - 图片是【辅助素材】，先由视觉模型转录成结构化文字，再作为「课堂笔记补充素材」
    注入纲目 / 撷要 / 笺注（不混入实录——实录是讲话逐字稿）。
  - 转录结果按「文件名 + mtime」缓存（session_store.read/write_note_cache），
    重复生成不重复调用视觉模型。
  - 用哪个模型：优先用户在模型配置面板手动指定的「图片识别模型」(store.get_vision)，
    未指定则自动选第一个【已配置且支持视觉】的供应商（全局默认优先）。
"""
from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from modules.business_school import prompts
from providers import ImagePart, Message, ProviderError, build_provider
from providers import store as provider_store
from storage import session_store

# 转录大图前的最长边（像素）：超过则降采样，控制 token 与请求体积
_MAX_EDGE = 1568
_TRANSCRIBE_MAX_TOKENS = 4000
_MAX_PARALLEL = 4


def resolve_vision_provider() -> Tuple[Optional[object], Optional[str]]:
    """解析图片识别用的 (provider, model)。

    1) 用户在配置面板手动指定且该供应商已配置 → 用它（尊重用户选择，不强制 supports_vision）；
    2) 否则自动选第一个【支持视觉且已配置】的供应商（全局默认优先）；
    3) 都没有 → (None, None)，由调用方提示用户去配置。
    """
    sel = provider_store.get_vision()
    if sel.get("id"):
        cfg = provider_store.get_config(sel["id"])
        if cfg:
            prov = build_provider(cfg)
            if prov.is_configured():
                return prov, (sel.get("model") or prov.default_model or None)

    did = provider_store.default_id()
    configs = sorted(provider_store.list_configs(), key=lambda c: 0 if c["id"] == did else 1)
    for c in configs:
        prov = build_provider(c)
        if prov.supports_vision and prov.is_configured():
            return prov, (prov.default_model or None)
    return None, None


def _prepare_image(data: bytes, media_type: str) -> Tuple[str, str]:
    """返回 (base64 字符串, media_type)。若 Pillow 可用且图片过大则降采样为 JPEG。
    Pillow 不可用或处理失败时，原样 base64 编码（优雅降级）。"""
    try:
        from PIL import Image  # 可选依赖

        im = Image.open(io.BytesIO(data))
        # 动图/带透明通道统一转 RGB，取首帧
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > _MAX_EDGE:
            scale = _MAX_EDGE / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:  # noqa: BLE001 — 任何失败都回退原图
        return base64.b64encode(data).decode("ascii"), media_type


def transcribe_image(provider, model, data: bytes, media_type: str, pre_prompt: str = "") -> str:
    """调用视觉模型把单张图片转录为 Markdown 文本。"""
    b64, mt = _prepare_image(data, media_type)
    msgs = [
        Message("system", prompts.note_transcribe_system(pre_prompt)),
        Message("user", prompts.note_transcribe_user(), images=[ImagePart(media_type=mt, data_b64=b64)]),
    ]
    res = provider.chat(msgs, model=model, temperature=0.2, max_tokens=_TRANSCRIBE_MAX_TOKENS)
    return (res.text or "").strip()


def collect_note_text(sid: str, pre_prompt: str = "") -> Tuple[str, dict]:
    """收集本会话所有图片素材的转录文本（带缓存），返回 (合并文本, 统计信息)。

    info 字段：images（图片数）、vision（是否有可用视觉模型）、provider（标签）、
    transcribed（本次新转录数）、cached（命中缓存数）、failed（转录失败数）。
    没有图片时 text 为空、images=0；有图片但无视觉模型时 vision=False（调用方应提示）。
    """
    images = session_store.list_image_inputs(sid)
    info = {"images": len(images), "vision": False, "provider": "",
            "transcribed": 0, "cached": 0, "failed": 0}
    if not images:
        return "", info

    # 先收集缓存未命中的，需要实际转录
    cached: dict = {}
    todo: List[str] = []
    for fn in images:
        c = session_store.read_note_cache(sid, fn)
        if c is not None:
            cached[fn] = c
        else:
            todo.append(fn)

    provider = model = None
    if todo:
        provider, model = resolve_vision_provider()
        if provider is None:
            # 无可用视觉模型：只能用已缓存的；其余跳过并提示
            info["cached"] = len(cached)
            blocks = [_note_block(fn, cached[fn]) for fn in images if fn in cached]
            return "\n\n".join(blocks), info

    info["vision"] = True
    info["provider"] = getattr(provider, "label", "") if provider else ""

    def _do(fn: str) -> Tuple[str, Optional[str]]:
        data = session_store.read_input_bytes(sid, fn)
        if data is None:
            return fn, None
        try:
            text = transcribe_image(provider, model, data, session_store.image_media_type(fn), pre_prompt)
        except ProviderError:
            return fn, None
        if text:
            session_store.write_note_cache(sid, fn, text)
        return fn, (text or None)

    results: dict = dict(cached)
    info["cached"] = len(cached)
    if todo:
        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as ex:
            for fn, text in ex.map(_do, todo):
                if text:
                    results[fn] = text
                    info["transcribed"] += 1
                else:
                    info["failed"] += 1

    blocks = [_note_block(fn, results[fn]) for fn in images if fn in results and results[fn]]
    return "\n\n".join(blocks), info


def _note_block(filename: str, text: str) -> str:
    return f"### 笔记照片：{filename}\n\n{text.strip()}"
