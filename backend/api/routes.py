"""P0 基础 API 路由。

涵盖：健康检查、板块列表、模型(provider)列表与连通性测试、会话增删查、
输入文件上传、产出读取与版本列表。处理流水线(生成四产出)在 P1 接入。
"""
from __future__ import annotations

import asyncio
import io
import json
import queue
import re
import time
from typing import List, Optional
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from config import APP_VERSION
from modules import get_module, list_modules
from pipeline import STEP_KEYS, available_artifacts, resolve_step_models, runner, vision
from providers import Message, ProviderError, build_provider, get_provider, list_providers
from providers import store as provider_store
from storage import persona as persona_store
from storage import session_store

router = APIRouter(prefix="/api")


# ---------- 健康检查 ----------
@router.get("/health")
def health() -> dict:
    # phase 随开发进度更新；version 取自根 VERSION（单一版本源）
    return {"status": "ok", "service": "meeting-minutes", "phase": "P3", "version": APP_VERSION}


# ---------- 板块 ----------
@router.get("/modules")
def modules() -> dict:
    return {"modules": list_modules()}


# ---------- 个人画像（全局，跨会话复用；会话可覆盖，见 PATCH /sessions）----------
def _persona_public() -> dict:
    """全局画像对外结构：text 为当前全局画像（空=未设置），default 为系统默认身份。"""
    return {"text": persona_store.get_global(), "default": persona_store.DEFAULT_PERSONA}


@router.get("/persona")
def get_persona() -> dict:
    return _persona_public()


class PersonaReq(BaseModel):
    text: str = ""  # 空串 = 清除全局画像、恢复系统默认


@router.put("/persona")
def set_persona(req: PersonaReq) -> dict:
    persona_store.set_global(req.text)
    return _persona_public()


# ---------- 模型 / Provider ----------
@router.get("/providers")
def providers() -> dict:
    return {
        "providers": list_providers(),
        "default_id": provider_store.default_id(),
        "vision": provider_store.get_vision(),  # 图片识别专用模型（空=自动）
    }


class VisionModelReq(BaseModel):
    provider_id: str = ""   # 空 = 清除手动指定，恢复自动选支持视觉的供应商
    model: str = ""


@router.put("/vision-model")
def set_vision_model(req: VisionModelReq) -> dict:
    """设置图片识别（课堂笔记照片转录）专用供应商/模型。"""
    try:
        return {"vision": provider_store.set_vision(req.provider_id, req.model)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _mask_key(key: str) -> str:
    """脱敏展示 API key：长度>10 显示 前4…后4，否则有 key 显示「已设置」，无 key 为空。"""
    key = key or ""
    if len(key) > 10:
        return f"{key[:4]}…{key[-4:]}"
    return "已设置" if key else ""


@router.get("/providers/{pid}")
def get_provider_detail(pid: str) -> dict:
    """取单个 provider 配置供编辑表单回填。

    安全：api_key 恒为空字符串，绝不回传明文；另给脱敏串与 has_key 布尔。
    编辑保存时不重敲 key（patch 不带 api_key）即保留原 key（update 走 exclude_unset）。
    """
    cfg = provider_store.get_config(pid)
    if not cfg:
        raise HTTPException(404, "provider 配置不存在")
    key = cfg.get("api_key") or ""
    out = dict(cfg)
    out["api_key"] = ""
    out["api_key_masked"] = _mask_key(key)
    out["has_key"] = bool(key)
    out["max_output_tokens"] = int(cfg.get("max_output_tokens") or 0)  # 老配置缺该字段时补 0
    return out


class ProviderConfigReq(BaseModel):
    label: str
    kind: str = "openai"  # openai | anthropic
    base_url: str = ""
    api_key: str = ""
    models: List[str] = []
    default_model: str = ""
    supports_vision: bool = False
    max_output_tokens: Optional[int] = None  # 输出上限（0/空=不限制）


@router.post("/providers")
def add_provider(req: ProviderConfigReq) -> dict:
    return provider_store.add(req.model_dump())


class ProviderPatchReq(BaseModel):
    label: Optional[str] = None
    kind: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    models: Optional[List[str]] = None
    default_model: Optional[str] = None
    supports_vision: Optional[bool] = None
    max_output_tokens: Optional[int] = None


@router.put("/providers/{pid}")
def edit_provider(pid: str, req: ProviderPatchReq) -> dict:
    cfg = provider_store.update(pid, req.model_dump(exclude_unset=True))
    if not cfg:
        raise HTTPException(404, "provider 配置不存在")
    return cfg


@router.delete("/providers/{pid}")
def remove_provider(pid: str) -> dict:
    if not provider_store.delete(pid):
        raise HTTPException(404, "provider 配置不存在")
    return {"deleted": pid, "default_id": provider_store.default_id()}


@router.put("/providers/{pid}/default")
def make_default_provider(pid: str) -> dict:
    if not provider_store.set_default(pid):
        raise HTTPException(404, "provider 配置不存在")
    return {"default_id": pid}


class ProviderTestReq(BaseModel):
    provider: Optional[str] = None        # 已存配置：按 id 测试
    config: Optional[ProviderConfigReq] = None  # 未存草稿：直接测试
    model: Optional[str] = None
    prompt: str = "用一句话确认你已就绪。"


@router.post("/providers/test")
def provider_test(req: ProviderTestReq) -> dict:
    """连通性测试：用最小调用验证某模型是否可用。
    支持测试已保存的配置（provider=id），也支持测试未保存的草稿（config）。
    """
    try:
        if req.config is not None:
            draft = req.config.model_dump()
            draft["id"] = "__draft__"
            # 「编辑已有供应商但没重敲 key」：草稿 key 为空且指明了 provider，则借用已存 key 测试。
            if not (draft.get("api_key") or "").strip() and req.provider:
                existing = provider_store.get_config(req.provider)
                if existing and existing.get("api_key"):
                    draft["api_key"] = existing["api_key"]
            p = build_provider(draft)
        else:
            p = get_provider(req.provider)
        if not p.is_configured():
            return {"ok": False, "error": f"{p.label} 未配置完整（缺 API key / url / 模型）。"}
        res = p.chat([Message("user", req.prompt)], model=req.model, max_tokens=64)
        return {"ok": True, "provider": res.provider, "model": res.model, "text": res.text}
    except ProviderError as e:
        return {"ok": False, "error": str(e)}


# ---------- 会话 ----------
class CreateSessionReq(BaseModel):
    module: str
    title: str
    pre_prompt: str = ""


@router.post("/sessions")
def create_session(req: CreateSessionReq) -> dict:
    mod = get_module(req.module)
    if not mod:
        raise HTTPException(404, f"未知板块：{req.module}")
    if not mod.enabled:
        raise HTTPException(400, f"板块「{mod.name}」暂未开放。")
    meta = session_store.create(req.module, req.title, req.pre_prompt)
    return _session_public(meta)


@router.get("/sessions")
def list_sessions(module: Optional[str] = None) -> dict:
    return {"sessions": [_session_public(m) for m in session_store.list(module)]}


@router.get("/sessions/{sid}")
def get_session(sid: str) -> dict:
    meta = session_store.get(sid)
    if not meta:
        raise HTTPException(404, "会话不存在")
    data = _session_public(meta, include_resolved=True)
    data["inputs"] = session_store.list_inputs(sid)
    return data


class UpdateSessionReq(BaseModel):
    title: Optional[str] = None
    pre_prompt: Optional[str] = None
    persona: Optional[str] = None  # 项目级画像；传空串=清除项目级、回退全局/系统默认


@router.patch("/sessions/{sid}")
def update_session(sid: str, req: UpdateSessionReq) -> dict:
    def apply(meta):
        if req.title is not None:
            meta.title = req.title
        if req.pre_prompt is not None:
            meta.pre_prompt = req.pre_prompt
        if req.persona is not None:
            # 空串=清除项目级画像（effective 会回退到全局/系统默认）。
            meta.persona = req.persona.strip()

    meta = session_store.mutate(sid, apply)
    if not meta:
        raise HTTPException(404, "会话不存在")
    return _session_public(meta)


@router.delete("/sessions/{sid}")
def delete_session(sid: str) -> dict:
    if not session_store.delete(sid):
        raise HTTPException(404, "会话不存在")
    return {"deleted": sid}


# ---------- 输入文件 ----------
@router.post("/sessions/{sid}/inputs")
async def upload_inputs(sid: str, files: List[UploadFile] = File(...)) -> dict:
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    saved = []
    for f in files:
        data = await f.read()
        try:
            p = session_store.save_input(sid, f.filename, data)
        except ValueError:
            raise HTTPException(400, f"文件名非法：{f.filename}")
        saved.append(p.name)
    return {"saved": saved, "inputs": session_store.list_inputs(sid)}


@router.delete("/sessions/{sid}/inputs/{filename}")
def delete_input(sid: str, filename: str) -> dict:
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    if not session_store.delete_input(sid, filename):
        raise HTTPException(404, "文件不存在或文件名非法")
    return {"deleted": filename, "inputs": session_store.list_inputs(sid)}


class RenameInputReq(BaseModel):
    new_name: str


@router.put("/sessions/{sid}/inputs/{filename}")
def rename_input(sid: str, filename: str, req: RenameInputReq) -> dict:
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    new_name = session_store.rename_input(sid, filename, req.new_name)
    if not new_name:
        raise HTTPException(400, "重命名失败：源文件不存在 / 名称非法 / 目标已存在")
    return {"renamed": {"from": filename, "to": new_name}, "inputs": session_store.list_inputs(sid)}


# ---------- 笔记照片转录：状态查询 / 用户校对 / 强制重转 ----------
def _require_image_input(sid: str, filename: str) -> None:
    """校验图片输入存在且确为图片类型；否则 404。会话存在性由调用方先行校验。"""
    from pathlib import Path

    if not session_store.input_path(sid, filename):
        raise HTTPException(404, "图片素材不存在")
    if Path(filename).suffix.lower() not in session_store.IMAGE_EXTS:
        raise HTTPException(404, "该文件不是图片素材")


@router.get("/sessions/{sid}/notes")
def list_notes(sid: str) -> dict:
    """列出本会话每张笔记照片的转录状态（user/cached/none 三态 + 当前生效文本）。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    return {"notes": [session_store.note_status(sid, fn)
                      for fn in session_store.list_image_inputs(sid)]}


class NoteEditReq(BaseModel):
    text: str = ""  # 空串 = 删除用户校对覆盖、恢复自动机器识别


@router.put("/sessions/{sid}/notes/{filename}")
def edit_note(sid: str, filename: str, req: NoteEditReq) -> dict:
    """保存用户对某张笔记照片的人工校对（空串=删除覆盖恢复自动）。返回该图新状态。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    _require_image_input(sid, filename)
    session_store.write_user_note(sid, filename, req.text)
    return session_store.note_status(sid, filename)


@router.post("/sessions/{sid}/notes/{filename}/transcribe")
def transcribe_note(sid: str, filename: str) -> dict:
    """强制重新机器转录该图（忽略一切缓存）。

    同步阻塞（单张图可接受，由 FastAPI 线程池执行）。成功写机器缓存并返回新状态；
    失败把具体原因放在 body（HTTP 200），便于前端直接展示、区分限流/无模型/图糊。
    注意：若存在用户校对覆盖，note_status 仍报 "user"（用户校对优先），
    故额外用 machine_text 字段回传本次机器结果供前端预览。"""
    meta = session_store.get(sid)
    if not meta:
        raise HTTPException(404, "会话不存在")
    _require_image_input(sid, filename)

    data = session_store.read_input_bytes(sid, filename)
    if data is None:
        return {"ok": False, "error": "读取图片失败（文件可能已被删除）",
                **session_store.note_status(sid, filename)}

    provider, model = vision.resolve_vision_provider()
    if provider is None:
        return {"ok": False,
                "error": "未配置可用的图片识别模型，请在右上角「模型配置」面板设置「图片识别模型」后重试。",
                **session_store.note_status(sid, filename)}

    try:
        text = vision.transcribe_image(
            provider, model, data, session_store.image_media_type(filename), meta.pre_prompt or ""
        )
    except ProviderError as e:
        return {"ok": False, "error": str(e) or "视觉模型调用失败",
                **session_store.note_status(sid, filename)}

    if not (text or "").strip():
        return {"ok": False, "error": "视觉模型返回空转录内容，请重试或更换图片识别模型。",
                **session_store.note_status(sid, filename)}

    session_store.write_note_cache(sid, filename, text)
    # machine_text 恒为本次机器结果；note_status 反映当前生效来源（有用户校对则仍为 user）。
    return {"ok": True, "machine_text": text, **session_store.note_status(sid, filename)}


# ---------- 生成（按步骤独立运行 + SSE 订阅）----------
def _require_step(step: str) -> None:
    """step 白名单校验：不在 STEP_KEYS 直接 400，避免越权 step 名进入运行器/文件层。"""
    if step not in STEP_KEYS:
        raise HTTPException(400, f"未知步骤：{step}")


@router.post("/sessions/{sid}/run-step")
def start_run_step(sid: str, step: str) -> dict:
    """启动单步生成任务。step ∈ transcript/chapters/minutes_concise/minutes_detailed/graph。
    幂等：同一 (sid, step) 已在跑就返回 already_running=True。
    """
    _require_step(step)
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    started = runner.start_step(sid, step)
    return {"started": started, "already_running": not started, "sid": sid, "step": step}


class ReviseStepReq(BaseModel):
    instruction: str


@router.post("/sessions/{sid}/revise-step")
def start_revise_step(sid: str, step: str, req: ReviseStepReq) -> dict:
    """启动单步修订：基于当前产出 + 修订意见再生成新版本。
    幂等：同一 (sid, step) 已在跑（生成或修订）就返回 already_running=True。
    进度复用 GET /run-step-stream?step=X（SSE）。
    """
    _require_step(step)
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    if not (req.instruction or "").strip():
        raise HTTPException(400, "请填写修订意见")
    started = runner.start_revise(sid, step, req.instruction)
    return {"started": started, "already_running": not started, "sid": sid, "step": step}


def _sse(evt: dict) -> str:
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


@router.get("/sessions/{sid}/run-step-stream")
def run_step_stream(sid: str, step: str) -> StreamingResponse:
    """订阅单步生成进度（SSE）。已结束则一次性回放历史。

    用 async 生成器 + 非阻塞轮询，避免把整条流放进同步生成器长期占用线程池线程
    （高并发订阅时会耗尽线程）。活跃任务用队列拉事件、空转让出事件循环、静默定期发 ping。
    """
    _require_step(step)
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")

    async def gen():
        q, run = runner.open_subscription(sid, step)
        if q is None:
            # 无活跃任务：一次性回放历史后结束。
            for evt in runner.load_history(sid, step):
                yield _sse(evt)
            return
        last_activity = time.monotonic()
        try:
            while True:
                try:
                    evt = q.get_nowait()
                except queue.Empty:
                    if time.monotonic() - last_activity >= 30:
                        last_activity = time.monotonic()
                        yield _sse({"type": "ping", "t": time.time()})
                    await asyncio.sleep(0.15)
                    continue
                last_activity = time.monotonic()
                if evt.get("type") == "_close":
                    return
                yield _sse(evt)
                if evt.get("type") in ("done", "error"):
                    return
        finally:
            runner.close_subscription(run, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{sid}/progress")
def get_progress(sid: str) -> dict:
    """一次性拉取所有步骤的进度（让前端首屏一次性恢复全部状态）。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    return {
        "running": runner.running_steps(sid),
        "by_step": {s: runner.load_history(sid, s) for s in STEP_KEYS},
    }


# ---------- 步骤模型配置（每个产出独立选模型）----------
class StepModelReq(BaseModel):
    step: str
    provider: Optional[str] = None
    model: Optional[str] = None


@router.put("/sessions/{sid}/step-model")
def set_step_model(sid: str, req: StepModelReq) -> dict:
    """设置某步骤使用的模型。provider/model 留空 = 重置为默认。"""
    def apply(meta):
        sm = dict(meta.step_models or {})
        if not req.provider and not req.model:
            sm.pop(req.step, None)
        else:
            sm[req.step] = {"provider": req.provider, "model": req.model}
        meta.step_models = sm

    meta = session_store.mutate(sid, apply)
    if not meta:
        raise HTTPException(404, "会话不存在")
    return {"step_models": meta.step_models}


# ---------- 产出 / 版本 ----------
@router.get("/sessions/{sid}/artifacts/{name}")
def get_artifact(sid: str, name: str) -> dict:
    # 老会话兼容：找不到新名时尝试其对应的老文件名
    from pipeline.engine import LEGACY_NAMES

    content = session_store.read_artifact(sid, name)
    if content is None:
        for legacy in LEGACY_NAMES.get(name, []):
            content = session_store.read_artifact(sid, legacy)
            if content is not None:
                break
    if content is None:
        raise HTTPException(404, "产出不存在（可能尚未生成）")
    return {"name": name, "content": content, "versions": _version_entries_compat(sid, name)}


@router.get("/sessions/{sid}/artifacts/{name}/download")
def download_artifact(sid: str, name: str) -> Response:
    """下载当前产出文件（含老会话文件名兼容）。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    content = _read_artifact_compat(sid, name)
    if content is None:
        raise HTTPException(404, "产出不存在（可能尚未生成）")
    return Response(
        content.encode("utf-8"),
        media_type=_artifact_media_type(name),
        headers={"Content-Disposition": _attachment_header(name)},
    )


@router.get("/sessions/{sid}/exports/bundle")
def export_bundle(sid: str) -> Response:
    """打包下载当前会话所有已生成产出。只导出产物，不包含原始输入素材。"""
    meta = session_store.get(sid)
    if not meta:
        raise HTTPException(404, "会话不存在")

    names = available_artifacts(sid)
    if not names:
        raise HTTPException(404, "暂无可导出的产出")

    buf = io.BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        for name in names:
            content = _read_artifact_compat(sid, name)
            if content is not None:
                zf.writestr(name, content)
    buf.seek(0)

    filename = f"{_safe_filename(meta.title) or '会议纪要'}_产出.zip"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


@router.get("/sessions/{sid}/artifacts/{name}/versions/{version}")
def get_artifact_version(sid: str, name: str, version: int) -> dict:
    content = _read_version_compat(sid, name, version)
    if content is None:
        raise HTTPException(404, "该版本不存在")
    return {"name": name, "version": version, "content": content}


@router.get("/sessions/{sid}/artifacts/{name}/versions/{version}/download")
def download_artifact_version(sid: str, name: str, version: int) -> Response:
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    content = _read_version_compat(sid, name, version)
    if content is None:
        raise HTTPException(404, "该版本不存在")
    filename = _version_filename(name, version)
    return Response(
        content.encode("utf-8"),
        media_type=_artifact_media_type(filename),
        headers={"Content-Disposition": _attachment_header(filename)},
    )


@router.post("/sessions/{sid}/artifacts/{name}/versions/{version}/restore")
def restore_artifact_version(sid: str, name: str, version: int) -> dict:
    """把某历史版本恢复为当前（写成一个新版本，不抹掉历史）。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    content = _read_version_compat(sid, name, version)
    if content is None:
        raise HTTPException(404, "该版本不存在")
    new_version = session_store.write_artifact(sid, name, content, note=f"恢复自 v{version}")
    return {"name": name, "restored_from": version, "version": new_version, "content": content}


# ---------- 助手 ----------
def _read_artifact_compat(sid: str, name: str) -> Optional[str]:
    """优先读规范化产出名；找不到时尝试老文件名。"""
    from pipeline.engine import LEGACY_NAMES

    content = session_store.read_artifact(sid, name)
    if content is not None:
        return content
    for legacy in LEGACY_NAMES.get(name, []):
        content = session_store.read_artifact(sid, legacy)
        if content is not None:
            return content
    return None


def _version_source_names(name: str) -> List[str]:
    """返回该规范产出名对应的旧名版本目录 + 当前目录。

    老会话在 v0.2 前后改过产出文件名，版本文件真实落在旧目录里；前端现在只按
    新文件名查询，所以这里把旧目录合并成一条连续版本线。
    """
    from pipeline.engine import LEGACY_NAMES

    names = list(LEGACY_NAMES.get(name, []))
    names.append(name)
    # 去重且保序，避免未来配置里出现重复别名。
    return list(dict.fromkeys(names))


def _version_entries_compat(sid: str, name: str) -> List[dict]:
    entries: List[dict] = []
    public_version = 1
    for source_name in _version_source_names(name):
        for item in session_store.list_versions(sid, source_name):
            entries.append({
                "version": public_version,
                "note": item.get("note", ""),
                "source_name": source_name,
                "source_version": item.get("version"),
            })
            public_version += 1
    return entries


def _read_version_compat(sid: str, name: str, version: int) -> Optional[str]:
    if version < 1:
        return None
    public_version = 1
    for source_name in _version_source_names(name):
        for item in session_store.list_versions(sid, source_name):
            if public_version == version:
                return session_store.read_version(sid, source_name, item["version"])
            public_version += 1
    return None


def _artifact_media_type(name: str) -> str:
    if name.lower().endswith(".md"):
        return "text/markdown; charset=utf-8"
    return "text/plain; charset=utf-8"


def _attachment_header(filename: str) -> str:
    # 同时给 ascii fallback 和 RFC 5987 filename*，保证中文文件名在主流浏览器中可用。
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]+', "_", (name or "").strip()).strip(" ._")


def _version_filename(name: str, version: int) -> str:
    dot = name.rfind(".")
    if dot > 0:
        return f"{name[:dot]}_v{version}{name[dot:]}"
    return f"{name}_v{version}"


def _session_public(meta, include_resolved: bool = False) -> dict:
    # 实时扫描已存在的产出（含老文件名 → 新名 映射），不再依赖 meta.artifacts 落后状态
    artifacts = available_artifacts(meta.id)
    data = {
        "id": meta.id,
        "module": meta.module,
        "title": meta.title,
        "pre_prompt": meta.pre_prompt,
        # 项目级画像原值（老会话缺该字段时 getattr 容错为 ""）。
        "persona": getattr(meta, "persona", "") or "",
        "step_models": meta.step_models or {},
        "status": meta.status,
        "artifacts": artifacts,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }
    if include_resolved:
        # 每步「最终生效」的 provider/model（含默认解析结果），供前端直接展示、免去重复逻辑。
        # 解析要遍历 provider 配置，只在会话详情下发，避免拖慢会话列表。
        data["resolved_step_models"] = resolve_step_models(meta)
        # 生效画像（项目>全局>默认），同 resolved_step_models：只在详情下发，避免拖慢列表。
        data["effective_persona"] = persona_store.effective(meta)
    return data
