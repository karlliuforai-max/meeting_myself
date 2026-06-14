"""板块(module)抽象：每个场景板块 = 语种 + 步骤定义 + 默认模型 + 产出。

P0 只定义结构与注册表；具体处理逻辑在 P1 的 pipeline 中填充。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class StepDef:
    key: str                 # 步骤标识，如 "transcript"
    title: str               # 展示名，如 "纠错整理逐字稿"
    output_name: str         # 产出文件名，如 "逐字稿.md"
    needs_vision: bool = False
    # 默认 provider/model 由模型配置面板（store 默认项）决定，不在此写死；
    # 用户可在每个产出处独立切换（见会话 step_models）。
    # 依赖的前置步骤 key 列表；执行前会检查这些步骤的产出是否都已存在。
    requires: List[str] = field(default_factory=list)
    # OR 依赖：列表中任一存在即可（用于"图谱依赖精炼版或详尽版"）
    requires_any: List[str] = field(default_factory=list)
    description: str = ""    # 给前端用户看的产出说明


@dataclass
class ModuleDef:
    key: str                 # 板块标识，如 "business_school"
    name: str                # 展示名，如 "学堂 · 商学院课堂讲座"
    languages: List[str] = field(default_factory=list)
    steps: List[StepDef] = field(default_factory=list)
    enabled: bool = True     # False = 平台上占位、暂未开发
    description: str = ""

    def public(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "languages": self.languages,
            "enabled": self.enabled,
            "description": self.description,
            "steps": [
                {
                    "key": s.key,
                    "title": s.title,
                    "output_name": s.output_name,
                    "requires": s.requires,
                    "requires_any": s.requires_any,
                    "description": s.description,
                }
                for s in self.steps
            ],
        }
