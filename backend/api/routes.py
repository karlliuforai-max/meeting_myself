"""P0 基础 API 路由。

涵盖：健康检查、板块列表、模型(provider)列表与连通性测试、会话增删查、
输入文件上传、产出读取与版本列表。处理流水线(生成四产出)在 P1 接入。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from modules import get_module, list_modules
from providers import Message, ProviderError, get_provider, list_providers
from storage import session_store

router = APIRouter(prefix="/api")


# ---------- 健康检查 ----------
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "meeting-minutes", "phase": "P0"}


# ---------- 板块 ----------
@router.get("/modules")
def modules() -> dict:
    return {"modules": list_modules()}


# ---------- 模型 / Provider ----------
@router.get("/providers")
def providers() -> dict:
    return {"providers": list_providers()}


class ProviderTestReq(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt: str = "用一句话确认你已就绪。"


@router.post("/providers/test")
def provider_test(req: ProviderTestReq) -> dict:
    """连通性测试：用最小调用验证某模型是否可用（前端"切换模型"时用）。"""
    try:
        p = get_provider(req.provider)
        if not p.is_configured():
            return {"ok": False, "error": f"{p.name} 未配置 API key。"}
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
    data = _session_public(meta)
    data["inputs"] = session_store.list_inputs(sid)
    return data


class UpdateSessionReq(BaseModel):
    title: Optional[str] = None
    pre_prompt: Optional[str] = None


@router.patch("/sessions/{sid}")
def update_session(sid: str, req: UpdateSessionReq) -> dict:
    meta = session_store.get(sid)
    if not meta:
        raise HTTPException(404, "会话不存在")
    if req.title is not None:
        meta.title = req.title
    if req.pre_prompt is not None:
        meta.pre_prompt = req.pre_prompt
    session_store.update(meta)
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
        session_store.save_input(sid, f.filename, data)
        saved.append(f.filename)
    return {"saved": saved, "inputs": session_store.list_inputs(sid)}


# ---------- 产出 / 版本 ----------
@router.get("/sessions/{sid}/artifacts/{name}")
def get_artifact(sid: str, name: str) -> dict:
    content = session_store.read_artifact(sid, name)
    if content is None:
        raise HTTPException(404, "产出不存在（可能尚未生成）")
    return {"name": name, "content": content, "versions": session_store.list_versions(sid, name)}


@router.get("/sessions/{sid}/artifacts/{name}/versions/{version}")
def get_artifact_version(sid: str, name: str, version: int) -> dict:
    content = session_store.read_version(sid, name, version)
    if content is None:
        raise HTTPException(404, "该版本不存在")
    return {"name": name, "version": version, "content": content}


# ---------- 助手 ----------
def _session_public(meta) -> dict:
    return {
        "id": meta.id,
        "module": meta.module,
        "title": meta.title,
        "pre_prompt": meta.pre_prompt,
        "status": meta.status,
        "artifacts": meta.artifacts,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }
