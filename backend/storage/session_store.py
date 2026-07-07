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
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable, List, Optional

from config import settings


def _now() -> float:
    return round(time.time(), 3)


def _safe_name(filename: str) -> Optional[str]:
    """把上传/操作的文件名净化为「纯文件名」，拒绝任何越界写入。

    约束：无论客户端传 "a/b"、"..\\x"、"../x" 还是纯 ".."，都不能落到 inputs/ 之外。
    统一把反斜杠视作分隔符后取 basename；结果为空或恰是 "."/".." 视为非法（返回 None）。
    """
    name = Path((filename or "").replace("\\", "/")).name.strip()
    if not name or name in (".", ".."):
        return None
    return name


def _decode_text(data: bytes) -> str:
    """稳健解码文本输入：优先 utf-8（带 BOM）→ 退 gb18030（国内常见 GBK 系）→ 最后 utf-8 容错替换。

    严格模式逐级尝试，避免把 GBK 文稿解成乱码；都不成时用 replace 保证不抛异常。
    """
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@dataclass
class SessionMeta:
    id: str
    module: str               # 板块 key，如 "business_school"
    title: str
    created_at: float
    updated_at: float
    pre_prompt: str = ""      # 任务前·自定义补充提示词（背景 + 重点要求）
    persona: str = ""         # 项目级学员画像（空=回退全局/系统默认，见 storage.persona）
    step_models: dict = field(default_factory=dict)   # 步骤 -> {provider, model}
    status: str = "created"   # created | processing | done | error
    artifacts: List[str] = field(default_factory=list)  # 已生成产出名


class SessionStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = (root or settings.data_path) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        # 守护 meta.json 的「读-改-写」复合操作，避免多个产出并行结束时互相覆盖
        self._lock = threading.RLock()

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
        # 原子写：先写临时文件再 rename，避免写到一半被读到损坏 JSON
        path = self._meta_path(meta.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)

    def mutate(self, sid: str, fn: Callable[[SessionMeta], None]) -> Optional[SessionMeta]:
        """在锁内对 meta 做「读-改-写」，fn 原地修改 meta。返回更新后的 meta（不存在则 None）。
        所有会修改 meta 的复合操作都应走这里，避免并发丢更新。"""
        with self._lock:
            meta = self.get(sid)
            if not meta:
                return None
            fn(meta)
            self._write_meta(meta)
            return meta

    def get(self, sid: str) -> Optional[SessionMeta]:
        p = self._meta_path(sid)
        if not p.exists():
            return None
        # 前向兼容：未来版本给 meta.json 增字段时，老代码读到未知键应忽略而非崩溃；
        # JSON 损坏或缺必填字段一律返回 None（list 天然跳过），避免整站因单个坏会话报错。
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            known = {f.name for f in fields(SessionMeta)}
            return SessionMeta(**{k: v for k, v in data.items() if k in known})
        except Exception:  # noqa: BLE001
            return None

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
        # 落盘前净化文件名，杜绝 "../" 之类路径穿越写到 inputs/ 之外。
        safe = _safe_name(filename)
        if not safe:
            raise ValueError("文件名非法")
        p = self._dir(sid) / "inputs" / safe
        p.write_bytes(data)
        return p

    # 图片素材（课堂笔记照片等）支持的扩展名
    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    _MEDIA_TYPES = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    }

    def list_inputs(self, sid: str) -> List[str]:
        d = self._dir(sid) / "inputs"
        # 只列普通文件，跳过派生缓存等子目录
        return sorted(f.name for f in d.iterdir() if f.is_file()) if d.exists() else []

    def list_image_inputs(self, sid: str) -> List[str]:
        """列出图片类输入文件名（课堂笔记照片等辅助素材）。"""
        return [f for f in self.list_inputs(sid) if Path(f).suffix.lower() in self.IMAGE_EXTS]

    def input_path(self, sid: str, filename: str) -> Optional[Path]:
        if "/" in filename or "\\" in filename or ".." in filename:
            return None
        p = self._dir(sid) / "inputs" / filename
        return p if p.exists() and p.is_file() else None

    def read_input_bytes(self, sid: str, filename: str) -> Optional[bytes]:
        p = self.input_path(sid, filename)
        return p.read_bytes() if p else None

    @classmethod
    def image_media_type(cls, filename: str) -> str:
        return cls._MEDIA_TYPES.get(Path(filename).suffix.lower(), "image/png")

    def delete_input(self, sid: str, filename: str) -> bool:
        """删除指定输入文件。文件名走基础校验避免越界（含反斜杠，与 input_path 一致）。"""
        if "/" in filename or "\\" in filename or ".." in filename:
            return False
        p = self._dir(sid) / "inputs" / filename
        if p.exists() and p.is_file():
            p.unlink()
            return True
        return False

    def rename_input(self, sid: str, old: str, new: str) -> Optional[str]:
        """重命名输入文件，返回新文件名；失败返回 None。
        保留原扩展名（前端只编辑主名），避免误改类型。
        """
        for s in (old, new):
            if "/" in s or "\\" in s or ".." in s or not s.strip():
                return None
        d = self._dir(sid) / "inputs"
        src = d / old
        if not src.exists() or not src.is_file():
            return None
        # 若 new 不带扩展名，沿用旧扩展名
        new = new.strip()
        if "." not in new:
            new = new + src.suffix
        dst = d / new
        if dst.exists() and dst != src:
            return None
        src.rename(dst)
        return dst.name

    def read_text_input_parts(self, sid: str) -> List[str]:
        """按文件名排序返回逐个文本输入（txt/md）的文本，便于对每个文件单独估时长等。"""
        d = self._dir(sid) / "inputs"
        if not d.exists():
            return []
        parts: List[str] = []
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in (".txt", ".md"):
                parts.append(_decode_text(f.read_bytes()))
        return parts

    def read_text_inputs(self, sid: str) -> str:
        """读取并拼接所有文本类输入（txt/md）。pdf/图片在 P3 处理。"""
        return "\n\n".join(self.read_text_input_parts(sid)).strip()

    # ---- 笔记照片转录缓存（按文件 mtime 失效，避免重复调用视觉模型）----
    def _note_cache_path(self, sid: str, filename: str) -> Optional[Path]:
        p = self.input_path(sid, filename)
        if not p:
            return None
        mtime = int(p.stat().st_mtime)
        safe = filename.replace("/", "_").replace("\\", "_")
        return self._dir(sid) / "derived" / "notes" / f"{safe}.{mtime}.md"

    def _user_note_path(self, sid: str, filename: str) -> Optional[Path]:
        """用户校对覆盖文件路径 derived/notes/<safe>.user.md（safe 转义同机器缓存）。

        与机器缓存不同：不带 mtime——用户校对是人工确认的结果，不该随图片改动自动失效。
        文件名须先通过 input_path 校验存在，杜绝路径穿越。"""
        p = self.input_path(sid, filename)
        if not p:
            return None
        safe = filename.replace("/", "_").replace("\\", "_")
        return self._dir(sid) / "derived" / "notes" / f"{safe}.user.md"

    def read_note_cache(self, sid: str, filename: str) -> Optional[str]:
        """读取该图片的转录文本：用户校对覆盖优先，其次机器缓存。

        用户覆盖（<safe>.user.md）存在即返回其内容，【不看 mtime】——人工校对结果
        不随图片改动失效；否则走机器缓存（图片不存在或 mtime 已变则返回 None）。"""
        up = self._user_note_path(sid, filename)
        if up and up.exists():
            return up.read_text(encoding="utf-8")
        cp = self._note_cache_path(sid, filename)
        if cp and cp.exists():
            return cp.read_text(encoding="utf-8")
        return None

    def write_note_cache(self, sid: str, filename: str, text: str) -> None:
        cp = self._note_cache_path(sid, filename)
        if not cp:
            return
        cp.parent.mkdir(parents=True, exist_ok=True)
        # 清掉同一文件的旧 mtime 缓存，避免无限堆积。
        # 注意：glob `<safe>.*.md` 会同时命中用户覆盖 `<safe>.user.md`，必须显式跳过，
        # 否则强制重转会误删用户人工校对（.user.md 不受 mtime 管理，见 read_note_cache）。
        for old in cp.parent.glob(f"{cp.name.rsplit('.', 2)[0]}.*.md"):
            if old != cp and old.name != f"{cp.name.rsplit('.', 2)[0]}.user.md":
                old.unlink(missing_ok=True)
        cp.write_text(text, encoding="utf-8")

    def write_user_note(self, sid: str, filename: str, text: str) -> bool:
        """写入/清除用户校对覆盖。text 为空串=删除覆盖（恢复自动机器识别）。

        返回是否成功；filename 须通过 input_path 校验存在（不存在返回 False）。"""
        up = self._user_note_path(sid, filename)
        if not up:
            return False
        if not (text or "").strip():
            # 空串=恢复自动：删掉覆盖文件即可，read_note_cache 回落到机器缓存。
            up.unlink(missing_ok=True)
            return True
        up.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再 os.replace，避免写到一半被读到半截。
        tmp = up.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, up)
        return True

    def note_status(self, sid: str, filename: str) -> dict:
        """单张图片的转录状态：user（有用户校对）/ cached（机器缓存有效）/ none（无）。

        text 为当前生效文本（user 用覆盖内容，cached 用机器缓存内容，none 为 None）。"""
        up = self._user_note_path(sid, filename)
        if up and up.exists():
            return {"filename": filename, "status": "user", "text": up.read_text(encoding="utf-8")}
        cp = self._note_cache_path(sid, filename)
        if cp and cp.exists():
            return {"filename": filename, "status": "cached", "text": cp.read_text(encoding="utf-8")}
        return {"filename": filename, "status": "none", "text": None}

    # ---- 产出（artifacts）与版本 ----
    def artifact_exists(self, sid: str, name: str) -> bool:
        """只判存在、不读内容（供大量存在性检查提速，如 available_artifacts）。"""
        return (self._dir(sid) / "artifacts" / name).exists()

    def read_artifact(self, sid: str, name: str) -> Optional[str]:
        p = self._dir(sid) / "artifacts" / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write_artifact(self, sid: str, name: str, content: str, note: str = "") -> int:
        """写入产出当前版本，并归档为新版本。返回新版本号（从 1 起）。"""
        with self._lock:
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
