"""P0 基础 API 路由。

涵盖：健康检查、板块列表、模型(provider)列表与连通性测试、会话增删查、
输入文件上传、产出读取与版本列表。处理流水线(生成四产出)在 P1 接入。
"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import APP_VERSION
from modules import get_module, list_modules
from pipeline import available_artifacts, run_stream, runner
from providers import Message, ProviderError, build_provider, get_provider, list_providers
from providers import store as provider_store
from storage import session_store

router = APIRouter(prefix="/api")


# ---------- 健康检查 ----------
@router.get("/health")
def health() -> dict:
    # phase 随开发进度更新；version 取自根 VERSION（单一版本源）
    return {"status": "ok", "service": "meeting-minutes", "phase": "P2", "version": APP_VERSION}


# ---------- 板块 ----------
@router.get("/modules")
def modules() -> dict:
    return {"modules": list_modules()}


# ---------- 模型 / Provider ----------
@router.get("/providers")
def providers() -> dict:
    return {"providers": list_providers(), "default_id": provider_store.default_id()}


@router.get("/providers/{pid}")
def get_provider_detail(pid: str) -> dict:
    """取单个 provider 完整配置（含 api_key，供编辑表单回填）。"""
    cfg = provider_store.get_config(pid)
    if not cfg:
        raise HTTPException(404, "provider 配置不存在")
    return cfg


class ProviderConfigReq(BaseModel):
    label: str
    kind: str = "openai"  # openai | anthropic
    base_url: str = ""
    api_key: str = ""
    models: List[str] = []
    default_model: str = ""
    supports_vision: bool = False


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
    data = _session_public(meta)
    data["inputs"] = session_store.list_inputs(sid)
    return data


class UpdateSessionReq(BaseModel):
    title: Optional[str] = None
    pre_prompt: Optional[str] = None


@router.patch("/sessions/{sid}")
def update_session(sid: str, req: UpdateSessionReq) -> dict:
    def apply(meta):
        if req.title is not None:
            meta.title = req.title
        if req.pre_prompt is not None:
            meta.pre_prompt = req.pre_prompt

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
        session_store.save_input(sid, f.filename, data)
        saved.append(f.filename)
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


# ---------- 生成（按步骤独立运行 + SSE 订阅）----------
@router.post("/sessions/{sid}/run-step")
def start_run_step(sid: str, step: str) -> dict:
    """启动单步生成任务。step ∈ transcript/chapters/minutes_concise/minutes_detailed/graph。
    幂等：同一 (sid, step) 已在跑就返回 already_running=True。
    """
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
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    if not (req.instruction or "").strip():
        raise HTTPException(400, "请填写修订意见")
    started = runner.start_revise(sid, step, req.instruction)
    return {"started": started, "already_running": not started, "sid": sid, "step": step}


@router.get("/sessions/{sid}/run-step-stream")
def run_step_stream(sid: str, step: str) -> StreamingResponse:
    """订阅单步生成进度（SSE）。已结束则一次性回放历史。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")

    def gen():
        try:
            for evt in runner.subscribe(sid, step):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

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
    steps = ["transcript", "chapters", "minutes_concise", "minutes_detailed", "graph"]
    return {
        "running": runner.running_steps(sid),
        "by_step": {s: runner.load_history(sid, s) for s in steps},
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
    return {"name": name, "content": content, "versions": session_store.list_versions(sid, name)}


@router.get("/sessions/{sid}/artifacts/{name}/versions/{version}")
def get_artifact_version(sid: str, name: str, version: int) -> dict:
    content = session_store.read_version(sid, name, version)
    if content is None:
        raise HTTPException(404, "该版本不存在")
    return {"name": name, "version": version, "content": content}


@router.post("/sessions/{sid}/artifacts/{name}/versions/{version}/restore")
def restore_artifact_version(sid: str, name: str, version: int) -> dict:
    """把某历史版本恢复为当前（写成一个新版本，不抹掉历史）。"""
    if not session_store.get(sid):
        raise HTTPException(404, "会话不存在")
    content = session_store.read_version(sid, name, version)
    if content is None:
        raise HTTPException(404, "该版本不存在")
    new_version = session_store.write_artifact(sid, name, content, note=f"恢复自 v{version}")
    return {"name": name, "restored_from": version, "version": new_version, "content": content}


# ---------- 助手 ----------
def _session_public(meta) -> dict:
    # 实时扫描已存在的产出（含老文件名 → 新名 映射），不再依赖 meta.artifacts 落后状态
    artifacts = available_artifacts(meta.id)
    return {
        "id": meta.id,
        "module": meta.module,
        "title": meta.title,
        "pre_prompt": meta.pre_prompt,
        "step_models": meta.step_models or {},
        "status": meta.status,
        "artifacts": artifacts,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }
