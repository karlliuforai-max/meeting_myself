"""后台生成任务运行器（按"会话+步骤"维度）。

每个 (session_id, step_key) 是一个独立任务；事件持久化到
  data/sessions/<sid>/progress/<step>.json
SSE 端点订阅特定步骤的事件流。客户端断开/刷新/换页面，回来后能继续看到累计进度。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

from config import settings
from pipeline import revise_one_step, run_one_step
from storage import session_store

# (sid, step_key) → 运行态
_RUNS: Dict[Tuple[str, str], "_Run"] = {}
_LOCK = threading.Lock()


class _Run:
    def __init__(self, sid: str, step: str):
        self.sid = sid
        self.step = step
        self.events: List[dict] = []
        self.subscribers: List[queue.Queue] = []
        self.alive = True
        self.lock = threading.Lock()

    def emit(self, evt: dict) -> None:
        with self.lock:
            self.events.append(evt)
            for q in list(self.subscribers):
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    pass
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
    try:
        (_progress_dir(sid) / f"{step}.json").write_text(
            json.dumps({"updated_at": time.time(), "events": events},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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


def subscribe(sid: str, step: str) -> Iterator[dict]:
    with _LOCK:
        run = _RUNS.get((sid, step))
    if not run:
        for evt in load_history(sid, step):
            yield evt
        return
    q = run.subscribe()
    try:
        while True:
            try:
                evt = q.get(timeout=30)
            except queue.Empty:
                yield {"type": "ping", "t": time.time()}
                continue
            if evt.get("type") == "_close":
                return
            yield evt
            if evt.get("type") in ("done", "error"):
                return
    finally:
        run.unsubscribe(q)


def load_history(sid: str, step: str) -> List[dict]:
    p = _progress_dir(sid) / f"{step}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("events", [])
    except Exception:  # noqa: BLE001
        return []
