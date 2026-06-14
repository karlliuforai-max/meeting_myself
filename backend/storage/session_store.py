"""本地会话存储层。

一个"会话(session)" = 一堂课/一次会议。存储结构（见开发文档 §9）：

    data/sessions/<id>/
      meta.json              板块、标题、前置提示词、各步模型配置、产出索引
      inputs/                原始 txt / pdf / 图片
      artifacts/             当前产出（逐字稿.md 等）
      versions/<产出>/        每轮修订版本 + 修订意见
      progress.json          断点续传状态

本层只做"读写本地文件"，对外是接口化的（未来上云时换实现即可）。
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from config import settings


def _now() -> float:
    return round(time.time(), 3)


@dataclass
class SessionMeta:
    id: str
    module: str               # 板块 key，如 "business_school"
    title: str
    created_at: float
    updated_at: float
    pre_prompt: str = ""      # 任务前·自定义补充提示词（背景 + 重点要求）
    step_models: dict = field(default_factory=dict)   # 步骤 -> {provider, model}
    status: str = "created"   # created | processing | done | error
    artifacts: List[str] = field(default_factory=list)  # 已生成产出名


class SessionStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = (root or settings.data_path) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 路径助手 ----
    def _dir(self, sid: str) -> Path:
        return self.root / sid

    def _meta_path(self, sid: str) -> Path:
        return self._dir(sid) / "meta.json"

    # ---- 会话生命周期 ----
    def create(self, module: str, title: str, pre_prompt: str = "") -> SessionMeta:
        sid = uuid.uuid4().hex[:12]
        d = self._dir(sid)
        (d / "inputs").mkdir(parents=True, exist_ok=True)
        (d / "artifacts").mkdir(parents=True, exist_ok=True)
        (d / "versions").mkdir(parents=True, exist_ok=True)
        meta = SessionMeta(
            id=sid, module=module, title=title,
            created_at=_now(), updated_at=_now(), pre_prompt=pre_prompt,
        )
        self._write_meta(meta)
        return meta

    def _write_meta(self, meta: SessionMeta) -> None:
        meta.updated_at = _now()
        self._meta_path(meta.id).write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, sid: str) -> Optional[SessionMeta]:
        p = self._meta_path(sid)
        if not p.exists():
            return None
        return SessionMeta(**json.loads(p.read_text(encoding="utf-8")))

    def update(self, meta: SessionMeta) -> SessionMeta:
        self._write_meta(meta)
        return meta

    def list(self, module: Optional[str] = None) -> List[SessionMeta]:
        out: List[SessionMeta] = []
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            m = self.get(d.name)
            if m and (module is None or m.module == module):
                out.append(m)
        out.sort(key=lambda m: m.updated_at, reverse=True)
        return out

    def delete(self, sid: str) -> bool:
        d = self._dir(sid)
        if d.exists():
            shutil.rmtree(d)
            return True
        return False

    # ---- 输入文件 ----
    def save_input(self, sid: str, filename: str, data: bytes) -> Path:
        p = self._dir(sid) / "inputs" / filename
        p.write_bytes(data)
        return p

    def list_inputs(self, sid: str) -> List[str]:
        d = self._dir(sid) / "inputs"
        return sorted(f.name for f in d.iterdir()) if d.exists() else []

    # ---- 产出（artifacts）与版本 ----
    def read_artifact(self, sid: str, name: str) -> Optional[str]:
        p = self._dir(sid) / "artifacts" / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write_artifact(self, sid: str, name: str, content: str, note: str = "") -> int:
        """写入产出当前版本，并归档为新版本。返回新版本号（从 1 起）。"""
        d = self._dir(sid)
        (d / "artifacts" / name).write_text(content, encoding="utf-8")

        vdir = d / "versions" / name
        vdir.mkdir(parents=True, exist_ok=True)
        version = len(list(vdir.glob("v*.md"))) + 1
        (vdir / f"v{version}.md").write_text(content, encoding="utf-8")
        if note:
            (vdir / f"v{version}.note.txt").write_text(note, encoding="utf-8")

        meta = self.get(sid)
        if meta and name not in meta.artifacts:
            meta.artifacts.append(name)
            self._write_meta(meta)
        return version

    def list_versions(self, sid: str, name: str) -> List[dict]:
        vdir = self._dir(sid) / "versions" / name
        if not vdir.exists():
            return []
        out = []
        for f in sorted(vdir.glob("v*.md"), key=lambda p: int(p.stem[1:])):
            note_path = f.with_suffix(".note.txt")
            out.append(
                {
                    "version": int(f.stem[1:]),
                    "note": note_path.read_text(encoding="utf-8") if note_path.exists() else "",
                }
            )
        return out

    def read_version(self, sid: str, name: str, version: int) -> Optional[str]:
        p = self._dir(sid) / "versions" / name / f"v{version}.md"
        return p.read_text(encoding="utf-8") if p.exists() else None


session_store = SessionStore()
