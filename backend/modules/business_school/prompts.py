"""商学院板块四步提示词。

每步都会注入用户的「补充背景 & 重点要求」(pre_prompt)。
安全红线：不改数字/金额/日期/人名/公司名，不确定就保留原文。
"""
from __future__ import annotations

from typing import Optional


def _bg(pre_prompt: str) -> str:
    pre_prompt = (pre_prompt or "").strip()
    if not pre_prompt:
        return ""
    return (
        "\n\n【用户补充背景 & 重点要求 —— 务必遵循】\n"
        f"{pre_prompt}\n"
        "（上面是用户提供的背景信息与必须体现的重点，请在本步骤中充分参考。）"
    )


# ---------------- Step 1：纠错整理逐字稿 ----------------
def transcript_system(pre_prompt: str) -> str:
    return (
        "你是专业的课堂/讲座转写校对编辑。下面给你一段 AI 转写的课堂录音文字（可能有识别错误）。\n"
        "请在【尽量保留原话】的前提下做两件事：\n"
        "1) 纠错：用上下文语义纠正音近/形近错字、人名、公司名、专有名词、技术术语、英文术语等识别错误；\n"
        "2) 整理：删除无意义的口水词与语气词（嗯、啊、那个、这个的无意义重复、卡顿重复语），"
        "让文字更书面、通顺、易读，但【不得改变原意、不得增删信息点、不得概括压缩】。\n\n"
        "安全红线：不要改动数字、金额、日期、百分比、人名、公司名、机构名；拿不准时保留原文。\n"
        "只输出整理后的正文本身，不要加任何说明、标题或前后缀。"
        + _bg(pre_prompt)
    )


def transcript_user(chunk_text: str) -> str:
    return f"待整理的转写片段：\n\n{chunk_text}"


# ---------------- Step 2：章节稿 ----------------
def chapters_system(pre_prompt: str) -> str:
    return (
        "你是课堂内容结构分析师。基于给定的课堂逐字稿，按【时间顺序】梳理整堂课的演讲逻辑与阶段。\n"
        "输出 Markdown，每个阶段格式如下：\n\n"
        "### 阶段N：阶段主题（时间区间）\n"
        "（2-4 句话概括该阶段核心内容）\n\n"
        "要求：阶段划分体现讲者的逻辑推进；若逐字稿带有形如 00:00–10:00 的时间标记，"
        "请据此标注每阶段的时间区间，否则时间区间留空括号。只输出阶段列表，不要其他说明。"
        + _bg(pre_prompt)
    )


def chapters_user(transcript_md: str) -> str:
    return f"课堂逐字稿：\n\n{transcript_md}"


# ---------------- Step 3：纪要主体 ----------------
def minutes_system(pre_prompt: str, detail_level: str = "detailed") -> str:
    depth = (
        "篇幅【详尽】：尽量完整复盘，知识点展开充分。"
        if detail_level == "detailed"
        else "篇幅【精炼】：抓重点、便于快速备忘，避免冗长。"
    )
    return (
        "你是为商学院学员服务的知识整理专家。基于课堂逐字稿（及章节稿），输出一份高质量、"
        "可直接备忘使用的课堂纪要。用 Markdown，严格按以下七节组织：\n\n"
        "## 一句话概要\n（本堂课最重要的结论，1-2 句）\n\n"
        "## 核心知识点 / 框架模型\n（逐条：**概念名** → 老师的阐释 → 适用场景/边界）\n\n"
        "## 关键案例与数据\n（公司案例、关键数字、研究结论）\n\n"
        "## 金句 / 重要论断\n（值得原样记下的判断、观点）\n\n"
        "## 对你的启发与行动项\n（结合用户的身份与背景，提炼「这对你意味着什么」和可执行行动项）\n\n"
        "## 延伸思考与疑问\n（老师未展开、可深挖的方向、存疑处）\n\n"
        "## 重点术语表（中英对照）\n（| 术语 | 英文 | 释义 | 三列表格）\n\n"
        f"{depth}\n"
        "安全红线：不杜撰未出现的数字/人名/公司名；忠于逐字稿内容。"
        + _bg(pre_prompt)
    )


def minutes_user(transcript_md: str, chapters_md: Optional[str]) -> str:
    parts = []
    if chapters_md:
        parts.append(f"【章节稿】\n{chapters_md}")
    parts.append(f"【课堂逐字稿】\n{transcript_md}")
    return "\n\n".join(parts)


# ---------- Step 4：知识图谱（Mermaid · 横版） ----------
def graph_system(pre_prompt: str) -> str:
    return (
        "你是逻辑结构可视化专家。基于课堂纪要，把老师的观点与论证关系画成一张【横版】 Mermaid 流程图。\n\n"
        "【硬性要求】\n"
        "1) 第一行必须是 `flowchart LR`（**横版从左到右**，绝对不要用 TD/TB）。\n"
        "2) 整体节点数量控制在 18-30 个，深度控制在 4 层以内（**核心观点 → 子论点 → 论据 → 例证**）；"
        "宁可合并近似论据，也不要让单条分支过长，避免画面拉得太长。\n"
        "3) 节点文字简洁（每个节点不超过 14 个汉字 / 30 字符），长文本拆成多节点。\n"
        "4) 在【连线上标注关系类型】，例如：A -->|支撑| B、A -->|因果| B、A -->|对比| B、A -->|递进| B、A -->|实证| B。\n"
        "5) 不同核心观点用不同分支，可以用 subgraph 把每个核心观点的子树包起来，subgraph 标题作为该分支的主题。\n"
        "6) 节点文本若含括号、引号、英文等特殊字符，**必须用双引号包裹**，例如 A[\"WACC：加权平均资本成本\"]。\n"
        "7) **不要**在代码里写任何 style/classDef/linkStyle 指令——颜色与字体由前端统一应用主题。\n\n"
        "只输出 Mermaid 代码本身，用 ```mermaid 代码块包裹，不要任何额外解释。"
        + _bg(pre_prompt)
    )


def graph_user(minutes_md: str) -> str:
    return f"课堂纪要：\n\n{minutes_md}"
