from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SelectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    title: str = Field(min_length=1)
    link: str = Field(min_length=1)
    date: str | None = None
    external_id: str | None = None


class RequestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=20, gt=0, le=120)
    interval_seconds: float = Field(default=1, ge=0, le=60)
    user_agent: str = Field(default="SiteAlertHubBot/1.0", min_length=1)


class CrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_items: int = Field(default=30, gt=0, le=500)
    max_retries: int = Field(default=3, ge=0, le=5)


class InitialSyncConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "baseline"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"baseline"}:
            raise ValueError("initial_sync.mode must be baseline")
        return value


class SourcePageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    site_name: str = Field(min_length=1, max_length=200)
    page_name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    bot_profile: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    list_url: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    parser: str = "generic_html"
    render_js: bool = False
    selectors: SelectorConfig
    request: RequestConfig = RequestConfig()
    crawl: CrawlConfig = CrawlConfig()
    initial_sync: InitialSyncConfig = InitialSyncConfig()


class BotProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    chat_id: str | int


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    supabase_url: str
    supabase_service_role_key: str
    telegram_bots_json: str
    source_pages_config: str = "config/source-pages.yml"
    max_delivery_attempts: int = Field(default=3, ge=1, le=10)
    delivery_retry_base_seconds: int = Field(default=60, ge=1, le=86_400)
    robots_timeout_seconds: float = Field(default=10, gt=0, le=60)
    http_timeout_seconds: float = Field(default=20, gt=0, le=120)

    @property
    def bot_profiles(self) -> dict[str, BotProfile]:
        try:
            raw: Any = json.loads(self.telegram_bots_json)
        except json.JSONDecodeError as exc:
            raise ValueError("TELEGRAM_BOTS_JSON must be valid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("TELEGRAM_BOTS_JSON must be an object")
        return {str(name): BotProfile.model_validate(value) for name, value in raw.items()}


def load_source_pages(path: str | Path) -> list[SourcePageConfig]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    raw_pages = document.get("source_pages")
    if not isinstance(raw_pages, list):
        raise ValueError("source_pages.yml must contain a source_pages list")
    return [SourcePageConfig.model_validate(page) for page in raw_pages]


def active_source_pages(pages: list[SourcePageConfig]) -> list[SourcePageConfig]:
    return [page for page in pages if page.enabled]
