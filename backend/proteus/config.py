"""Runtime configuration and encryption helpers."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings supplied by the deployment environment."""

    model_config = SettingsConfigDict(env_prefix='PROTEUS_', extra='ignore')

    database_url: str = 'postgresql://postgres:proteus_local@localhost:55432/proteus'
    sandbox_url: str = 'postgresql://postgres:proteus_local@localhost:55433/postgres'
    mode: str = 'local'
    secret_key: str = 'development-only-change-before-sharing'
    static_dir: Path | None = None
    cookie_secure: bool = False
    demo_ttl_hours: int = Field(default=24, ge=1, le=168)
    max_projects: int = Field(default=10, ge=1, le=100)
    max_branches: int = Field(default=10, ge=1, le=100)
    connection_timeout_seconds: int = Field(default=10, ge=1, le=60)

    def model_post_init(self, __context: object) -> None:
        if self.mode not in {'local', 'demo'}:
            raise ValueError("PROTEUS_MODE must be 'local' or 'demo'")

    @property
    def fernet(self) -> Fernet:
        material = hashlib.sha256(self.secret_key.encode('utf-8')).digest()
        return Fernet(base64.urlsafe_b64encode(material))


@lru_cache
def get_settings() -> Settings:
    return Settings()
