"""Pydantic Settings, one instance per environment.

A single `Settings` class with nested groups (`settings.database.url`,
`settings.jwt.secret_key`, `settings.telegram.webhook_base_url`, etc.)
instead of everything flattened at one level.

The env file is picked from the `ENVIRONMENT` env var
(`development` | `staging` | `production`), falling back to `.env.development`.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://renot:renot@localhost:5432/renot_dev"
    pool_size: int = 10
    max_overflow: int = 20


class JWTSettings(BaseModel):
    # TODO: MUST be overridden via env var in staging/production - never use this default.
    secret_key: str = "changeme-generate-a-strong-secret"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"


class CelerySettings(BaseModel):
    broker_url: str = "redis://localhost:6379/1"
    result_backend: str = "redis://localhost:6379/2"


class TelegramSettings(BaseModel):
    webhook_base_url: str = "https://your-domain.example.com"
    # Tutorial link sent back by the bot's `/help` command.
    help_url: str = "https://your-domain.example.com/docs/telegram-bot-setup"


class CORSSettings(BaseModel):
    # Strict per-environment whitelist - never "*" in staging/production.
    allow_origins: list[str] = ["http://localhost:3000"]


class I18nSettings(BaseModel):
    default_language: str = "en"


class TimezoneSettings(BaseModel):
    default_timezone: str = "Asia/Jakarta"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=f".env.{os.getenv('ENVIRONMENT', 'development')}",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: str = "development"
    app_name: str = "Renot API"

    database: DatabaseSettings = DatabaseSettings()
    jwt: JWTSettings = JWTSettings()
    redis: RedisSettings = RedisSettings()
    celery: CelerySettings = CelerySettings()
    telegram: TelegramSettings = TelegramSettings()
    cors: CORSSettings = CORSSettings()
    i18n: I18nSettings = I18nSettings()
    timezone: TimezoneSettings = TimezoneSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
