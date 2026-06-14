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
    openai_compat_api_key: Optional[str] = None
    openai_compat_base_url: Optional[str] = None

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
