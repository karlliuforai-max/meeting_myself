"""会议纪要生成平台 · 后端入口（FastAPI）。

开发态：前端跑在 Vite(5173)，通过 CORS 访问本服务(8000)。
生产态：前端打包为静态文件由本服务直接托管（见文末 static 挂载）——届时只需启动后端。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router
from config import APP_VERSION

app = FastAPI(title="会议纪要生成平台", version=APP_VERSION)

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
