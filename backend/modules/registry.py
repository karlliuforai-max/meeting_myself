"""板块注册表。

本期开发"学堂"（商学院）。其余板块在平台上**占位**（enabled=False），
预留扩展端口：未来新增板块只需 import 其 MODULE 并注册即可。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from modules.base import ModuleDef
from modules.business_school import MODULE as BUSINESS_SCHOOL

# 预留占位板块（暂未开发）
_MINING = ModuleDef(
    key="mining",
    name="矿山 · 矿业商务沟通",
    languages=["四川话", "乐山话", "普通话"],
    enabled=False,
    description="四川话/乐山话夹杂普通话的矿业商务沟通（未来接入 sichuan-mining-transcribe）。",
)
_DAILY = ModuleDef(
    key="daily",
    name="闲谈 · 日常聊天",
    languages=["普通话"],
    enabled=False,
    description="日常聊天中内涵价值较高的交流整理。",
)
_GENERAL = ModuleDef(
    key="general",
    name="通用",
    languages=["任意"],
    enabled=False,
    description="通用会议纪要输出，适配未归类场景。",
)

_MODULES: Dict[str, ModuleDef] = {
    m.key: m for m in (BUSINESS_SCHOOL, _MINING, _DAILY, _GENERAL)
}


def get_module(key: str) -> Optional[ModuleDef]:
    return _MODULES.get(key)


def list_modules() -> List[dict]:
    return [m.public() for m in _MODULES.values()]
