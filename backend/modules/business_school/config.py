"""商学院课堂讲座板块（暂名"学堂"）配置。

四产出对应四个处理步骤（逻辑在 P1 实现）：
逐字稿 / 章节稿 / 纪要主体 / 知识图谱。默认全部使用 Claude。
"""
from __future__ import annotations

from modules.base import ModuleDef, StepDef

MODULE = ModuleDef(
    key="business_school",
    name="学堂 · 商学院课堂讲座",
    languages=["普通话", "英语"],
    enabled=True,
    description="普通话/英语为主的商学院课堂与讲座，输出逐字稿、章节稿、纪要主体、知识图谱。",
    steps=[
        StepDef(
            key="transcript",
            title="纠错整理逐字稿",
            output_name="逐字稿.md",
            default_provider="claude",
        ),
        StepDef(
            key="chapters",
            title="章节稿（阶段主题+时间区间）",
            output_name="章节稿.md",
            default_provider="claude",
        ),
        StepDef(
            key="minutes",
            title="纪要主体",
            output_name="纪要主体.md",
            default_provider="claude",
        ),
        StepDef(
            key="graph",
            title="知识图谱（Mermaid）",
            output_name="知识图谱.mmd",
            default_provider="claude",
        ),
    ],
)
