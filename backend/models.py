from __future__ import annotations

from pydantic import BaseModel, Field

from config import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_RETENTION_MONTHS,
    MIN_RETENTION_MONTHS,
    MAX_RETENTION_MONTHS,
)


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=3, max_length=255)


class SourceUpdate(BaseModel):
    is_active: bool | None = None
    ai_enabled: bool | None = None
    digest_enabled: bool | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    is_default: bool = False


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    is_default: bool | None = None


class KeywordCreate(BaseModel):
    phrase: str = Field(min_length=2, max_length=120)
    category_id: int | None = None
    min_score: int = Field(default=0, ge=0, le=10)
    is_regex: bool = False


class IntegrationsPayload(BaseModel):
    claude_api_key: str | None = None
    claude_model: str | None = DEFAULT_CLAUDE_MODEL
    claude_model_2: str | None = None
    claude_model_3: str | None = None
    grok_api_key: str | None = None
    grok_model: str | None = None
    grok_model_2: str | None = None
    grok_model_3: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_model_2: str | None = None
    gemini_model_3: str | None = None
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_bot_token: str | None = None
    telegram_unknown_forward_enabled: bool | None = None
    telegram_unknown_forward_primary: str | None = None
    telegram_unknown_forward_reserve: str | None = None


class TelethonCodeRequest(BaseModel):
    phone: str


class TelethonCodeVerify(BaseModel):
    phone: str
    code: str
    password: str | None = None


class MonitorConfigPayload(BaseModel):
    collect_enabled: bool
    ai_enabled: bool
    retention_months: int = Field(
        default=DEFAULT_RETENTION_MONTHS,
        ge=MIN_RETENTION_MONTHS,
        le=MAX_RETENTION_MONTHS,
    )
    ai_prompt: str | None = None
    dedup_enabled: bool = True
    ai_provider: str = Field(default="claude", pattern=r"^(claude|grok|gemini)$")
    ai_model: str | None = None


class ClearMessagesPayload(BaseModel):
    confirm: bool = False


class PromptTokensPayload(BaseModel):
    ai_prompt: str | None = None


class DashboardHeartbeatPayload(BaseModel):
    session_key: str = Field(min_length=8, max_length=120)
    active_seconds: int = Field(default=0, ge=0, le=86400)
    language: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    screen: str | None = Field(default=None, max_length=32)
    path: str | None = Field(default=None, max_length=120)


class AlertCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    alert_type: str = Field(pattern=r"^(new_message|min_score|keyword_ai)$")
    source_id: int | None = None
    min_score: int | None = Field(default=None, ge=0, le=10)
    pattern: str = Field(default="", max_length=500)
    target_chat_id: str = Field(min_length=3, max_length=64)
    is_enabled: bool = True
    is_ai_keyword: bool = True


class DigestConfigPayload(BaseModel):
    enabled: bool = False
    hour: int = Field(default=10, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    min_score: int = Field(default=6, ge=1, le=10)
    max_per_category: int = Field(default=5, ge=1, le=20)
    excluded_categories: list[str] = []
    ai_prompt: str = Field(default="", max_length=2000)
    keep_days: int = Field(default=30, ge=1, le=365)
    ai_provider: str = Field(default="claude", pattern=r"^(claude|grok|gemini)$")
    ai_model: str | None = None
    mode: str = Field(default="previous_day", pattern=r"^(previous_24h|previous_day)$")


class AlertUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    alert_type: str | None = Field(
        default=None, pattern=r"^(new_message|min_score|keyword_ai)$"
    )
    source_id: int | None = None
    min_score: int | None = Field(default=None, ge=0, le=10)
    pattern: str | None = Field(default=None, max_length=500)
    target_chat_id: str | None = Field(default=None, min_length=3, max_length=64)
    is_enabled: bool | None = None
    is_ai_keyword: bool | None = None
