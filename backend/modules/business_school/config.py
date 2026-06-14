"""商学院课堂讲座板块（暂名"学堂"）配置。

五个产出（文艺命名 · 一脉相承的知识叙事）：
  ① 实录    transcript        基础，后续一切的前置
  ② 纲目    chapters          依赖 ①
  ③ 撷要    minutes_concise   依赖 ①
  ④ 笺注    minutes_detailed  依赖 ①
  ⑤ 脉络    graph             依赖 ③ 或 ④

默认 provider 为 deepseek（在 .env 配置）；用户可在每个产出处独立切换。
"""
from __future__ import annotations

from modules.base import ModuleDef, StepDef

MODULE = ModuleDef(
    key="business_school",
    name="学堂 · 商学院课堂讲座",
    languages=["普通话", "英语"],
    enabled=True,
    description="普通话/英语为主的商学院课堂与讲座，输出实录、纲目、撷要、笺注、脉络。",
    steps=[
        StepDef(
            key="transcript",
            title="实录",
            output_name="实录.md",
            description="基于原始转写稿，去口水词、纠正音近形近字与人名公司名术语；按 10 分钟分段。是后续一切的基础。",
        ),
        StepDef(
            key="chapters",
            title="纲目",
            output_name="纲目.md",
            requires=["transcript"],
            description="按时间顺序梳理整堂课的主线骨架，每阶段附时间区间与核心概括。",
        ),
        StepDef(
            key="minutes_concise",
            title="撷要",
            output_name="撷要.md",
            requires=["transcript"],
            description="撷取课堂精华的精炼纪要，七节结构含中英术语表。便于快速回看。",
        ),
        StepDef(
            key="minutes_detailed",
            title="笺注",
            output_name="笺注.md",
            requires=["transcript"],
            description="如古籍笺注般完整深入的详尽纪要，知识点充分展开。便于深度学习。",
        ),
        StepDef(
            key="graph",
            title="脉络",
            output_name="脉络.mmd",
            requires_any=["minutes_concise", "minutes_detailed"],
            description="把老师观点、子论点、论据画成横版思维流程图；连线标注 支撑/递进/因果/对比 等关系。",
        ),
    ],
)
