"""会议纪要生成平台 · 后端入口（FastAPI）。

开发态：前端跑在 Vite(5173)，通过 CORS 访问本服务(8000)。
生产态：前端打包为静态文件由本服务直接托管（见文末 static 挂载）——届时只需启动后端。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router
from config import APP_VERSION
from pipeline import runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时清理僵尸任务：上次进程被 kill/重启时正在跑的步骤，补一条中断 error，
    # 否则前端会永远卡在「进行中」。用 lifespan 而非已废弃的 on_event。
    # 扫描是磁盘密集型（全部会话 × 全部步骤），放线程池执行，不阻塞事件循环起服务。
    try:
        n = await asyncio.to_thread(runner.mark_interrupted_runs)
        if n:
            print(f"[startup] 标记 {n} 个因重启中断的生成任务为失败。")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] 清理僵尸任务失败：{e}")
    yield


app = FastAPI(title="会议纪要生成平台", version=APP_VERSION, lifespan=lifespan)

# 开发态跨域：允许本地前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# 生产态：若前端已构建到 frontend/dist，则直接托管（用户只需启动后端）
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")


@app.get("/api")
def api_root() -> dict:
    return {"service": "meeting-minutes", "docs": "/docs"}
