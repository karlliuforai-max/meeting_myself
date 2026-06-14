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
    default_provider: str = "claude"
    default_model: str = ""  # 空 = 用 provider 默认
    needs_vision: bool = False


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
                {"key": s.key, "title": s.title, "output_name": s.output_name}
                for s in self.steps
            ],
        }
