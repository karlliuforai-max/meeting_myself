"""全局配置：从环境变量 / .env 读取。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 模型凭据
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None

    # 第三方 / 自定义 OpenAI 兼容端点（四要素）
    openai_compat_label: str = "自定义模型"        # Provider 显示名
    openai_compat_base_url: Optional[str] = None    # url
    openai_compat_api_key: Optional[str] = None     # apikey
    openai_compat_model: Optional[str] = None       # model（默认模型）
    openai_compat_vision: bool = False              # 该模型是否支持视觉（看图）

    # 第三方 Anthropic 兼容中转站（Messages 原生格式，四要素）
    anthropic_compat_label: str = "Anthropic 中转站"
    anthropic_compat_base_url: Optional[str] = None
    anthropic_compat_api_key: Optional[str] = None
    anthropic_compat_model: Optional[str] = None
    anthropic_compat_vision: bool = False

    # 默认 provider / 模型
    default_provider: str = "claude"
    default_model: Optional[str] = None

    # 数据目录（会话存储）。默认 <项目根>/data
    data_dir: Optional[str] = None

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir) if self.data_dir else (PROJECT_ROOT / "data")
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
