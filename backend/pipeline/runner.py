"""后台生成任务运行器（按"会话+步骤"维度）。

每个 (session_id, step_key) 是一个独立任务；事件持久化到
  data/sessions/<sid>/progress/<step>.json
SSE 端点订阅特定步骤的事件流。客户端断开/刷新/换页面，回来后能继续看到累计进度。
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import settings
from pipeline import STEP_KEYS, revise_one_step, run_one_step
from storage import session_store

# (sid, step_key) → 运行态
_RUNS: Dict[Tuple[str, str], "_Run"] = {}
_LOCK = threading.Lock()

# 防御性 step 校验：即便上游漏检，也不让越权 step 名穿透到文件系统。
_STEP_RE = re.compile(r"^[a-z_]+$")


class _Run:
    def __init__(self, sid: str, step: str):
        self.sid = sid
        self.step = step
        self.events: List[dict] = []
        self.subscribers: List[queue.Queue] = []
        self.alive = True
        self.lock = threading.Lock()

    def emit(self, evt: dict) -> None:
        # 瞬态事件（如流式「已生成约 N 字」）只推给在线订阅者，不进 events、不落盘：
        # 它们量大且无回放价值，落盘会把 progress.json 撑爆、拖慢持久化。
        transient = bool(evt.get("transient"))
        with self.lock:
            if not transient:
                self.events.append(evt)
            for q in list(self.subscribers):
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    pass
        if not transient:
            _persist(self.sid, self.step, self.events)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1024)
        with self.lock:
            for evt in self.events:
                q.put_nowait(evt)
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def finish(self) -> None:
        self.alive = False
        with self.lock:
            for q in self.subscribers:
                try:
                    q.put_nowait({"type": "_close"})
                except queue.Full:
                    pass


def _progress_dir(sid: str) -> Path:
    p = settings.data_path / "sessions" / sid / "progress"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _persist(sid: str, step: str, events: List[dict]) -> None:
    if not _STEP_RE.match(step or ""):
        return  # 非法 step 名不写盘
    try:
        path = _progress_dir(sid) / f"{step}.json"
        tmp = path.with_suffix(".json.tmp")
        # 原子写：先写临时文件再 rename，避免并发/崩溃留下半截 JSON。
        tmp.write_text(
            json.dumps({"updated_at": time.time(), "events": events},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def is_running(sid: str, step: str) -> bool:
    with _LOCK:
        run = _RUNS.get((sid, step))
        return bool(run and run.alive)


def running_steps(sid: str) -> List[str]:
    out: List[str] = []
    with _LOCK:
        for (s, step), run in _RUNS.items():
            if s == sid and run.alive:
                out.append(step)
    return out


def _start(sid: str, step: str, events_factory, *, tag: str) -> bool:
    """通用任务启动：events_factory() 返回事件生成器。同一 (sid, step) 已在跑则返回 False。
    生成与修订共用同一 step 维度（同一产出同一时间只允许一个任务）。"""
    with _LOCK:
        key = (sid, step)
        if key in _RUNS and _RUNS[key].alive:
            return False
        run = _Run(sid, step)
        _RUNS[key] = run

    def worker():
        try:
            for evt in events_factory():
                run.emit(evt)
        except Exception as e:  # noqa: BLE001
            run.emit({"type": "error", "step": step,
                      "message": f"运行器异常：{e}", "t": time.time()})
        finally:
            run.finish()
            # 完成即从内存表移除，避免长跑累积；历史已落盘，
            # 之后的订阅会回退到 load_history（见 subscribe）。
            with _LOCK:
                if _RUNS.get(key) is run:
                    del _RUNS[key]

    threading.Thread(target=worker, name=f"{tag}-{sid}-{step}", daemon=True).start()
    return True


def start_step(sid: str, step: str) -> bool:
    """启动单步生成任务。已在跑则返回 False。"""
    return _start(sid, step, lambda: run_one_step(sid, step), tag="gen")


def start_revise(sid: str, step: str, instruction: str) -> bool:
    """启动单步修订任务（基于当前产出 + 修订意见 → 新版本）。已在跑则返回 False。"""
    return _start(sid, step, lambda: revise_one_step(sid, step, instruction), tag="rev")


def load_history(sid: str, step: str) -> List[dict]:
    if not _STEP_RE.match(step or ""):
        return []  # 非法 step 名直接返回空，不去碰文件系统
    p = _progress_dir(sid) / f"{step}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("events", [])
    except Exception:  # noqa: BLE001
        return []


def open_subscription(sid: str, step: str) -> Tuple[Optional[queue.Queue], Optional["_Run"]]:
    """SSE 用：若该步骤有活跃任务，返回 (预置了历史事件的订阅队列, run)；否则 (None, None)，
    调用方应改为一次性回放 load_history。在 _LOCK 内 subscribe，避免与 finish 竞态漏事件。"""
    with _LOCK:
        run = _RUNS.get((sid, step))
        if run and run.alive:
            return run.subscribe(), run
    return None, None


def close_subscription(run: Optional["_Run"], q: Optional[queue.Queue]) -> None:
    if run is not None and q is not None:
        run.unsubscribe(q)


def mark_interrupted_runs() -> int:
    """服务重启时调用：把「上次进程里没跑完就被中断」的任务补一条 error 事件，
    否则前端会一直显示进行中、SSE 也永远等不到 done/error。

    判定：progress/<step>.json（step 限 STEP_KEYS）的末事件 type 不在 (done, error)，
    且当前内存里没有该 (sid, step) 的活跃任务。返回补写的任务数。"""
    sessions_dir = settings.data_path / "sessions"
    if not sessions_dir.exists():
        return 0
    marked = 0
    for sdir in sessions_dir.iterdir():
        if not sdir.is_dir():
            continue
        sid = sdir.name
        pdir = sdir / "progress"
        if not pdir.exists():
            continue
        for step in STEP_KEYS:
            p = pdir / f"{step}.json"
            if not p.exists():
                continue
            if is_running(sid, step):
                continue
            events = load_history(sid, step)
            if not events:
                continue
            last_type = events[-1].get("type")
            if last_type in ("done", "error"):
                continue
            events.append({
                "type": "error", "step": step,
                "message": "任务因服务重启而中断，请重新生成。", "t": time.time(),
            })
            _persist(sid, step, events)
            marked += 1
    return marked
