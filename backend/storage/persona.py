"""个人画像存储层。

「画像(persona)」= 听课学员本人的身份与背景（如「消费投研分析师，关注新能源」），
用于把纪要里「对你的启发」个性化——判断哪些内容对该学员更有价值。

两级来源：
  - 全局画像：data/persona.txt，一份文件、跨会话复用（用户在设置里维护一次）；
  - 项目级画像：写在会话 meta.persona，覆盖全局（仅对该会话生效）。

优先级（effective）：项目级 > 全局 > 系统默认。
本层只做「读写本地文件 + 合成生效值」，与 session_store 同为可替换的存储实现。
"""
from __future__ import annotations

import os
from typing import Optional

from config import settings

# 系统默认画像：用户从未设置任何画像时的兜底身份，保证提示词里「对你的启发」有泛化落点。
DEFAULT_PERSONA = "商学院学生"


def _persona_path():
    return settings.data_path / "persona.txt"


def get_global() -> str:
    """读取全局画像；文件不存在或内容为空一律返回 ""（表示未设置、走系统默认）。"""
    p = _persona_path()
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_global(text: str) -> str:
    """保存全局画像（strip 后原子写）。

    传空串 = 清除，语义为「恢复系统默认」：删除文件即可（读回为 ""）。
    返回保存后的文本（清除时为 ""）。
    """
    text = (text or "").strip()
    p = _persona_path()
    if not text:
        # 清除：删掉文件，get_global() 会返回 ""，effective 回退到系统默认。
        p.unlink(missing_ok=True)
        return ""
    # 原子写：先写临时文件再 os.replace，避免写到一半被读到半截内容。
    tmp = p.with_suffix(".txt.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return text


def effective(meta) -> dict:
    """合成该会话「最终生效」的画像与来源。

    优先级：meta.persona（strip 非空）> 全局画像 > 系统默认。
    meta 可能是老对象、没有 persona 字段，用 getattr 容错。
    返回 {"text": ..., "source": "project"|"global"|"default"}。
    """
    project = (getattr(meta, "persona", "") or "").strip()
    if project:
        return {"text": project, "source": "project"}
    g = get_global()
    if g:
        return {"text": g, "source": "global"}
    return {"text": DEFAULT_PERSONA, "source": "default"}
