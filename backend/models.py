from __future__ import annotations

from pydantic import BaseModel, Field

from config import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_MONITOR_DEPTH,
    DEFAULT_MAX_MESSAGES,
    MIN_MONITOR_INTERVAL_SECONDS,
    MAX_MONITOR_INTERVAL_SECONDS,
    MONITOR_INTERVAL_SECONDS,
    MIN_MONITOR_DEPTH,
    MAX_MONITOR_DEPTH,
    MIN_MAX_MESSAGES,
    MAX_MAX_MESSAGES,
)


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=3, max_length=255)


class SourceUpdate(BaseModel):
    is_active: bool | None = None
    ai_enabled: bool | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    is_default: bool = False


class KeywordCreate(BaseModel):
    phrase: str = Field(min_length=2, max_length=120)
    category_id: int | None = None
    min_score: int = Field(default=0, ge=0, le=10)
    is_regex: bool = False


class IntegrationsPayload(BaseModel):
    claude_api_key: str | None = None
    claude_model: str | None = DEFAULT_CLAUDE_MODEL
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_bot_token: str | None = None


class TelethonCodeRequest(BaseModel):
    phone: str


class TelethonCodeVerify(BaseModel):
    phone: str
    code: str
    password: str | None = None


class MonitorConfigPayload(BaseModel):
    collect_enabled: bool
    ai_enabled: bool
    interval_seconds: int = Field(
        default=MONITOR_INTERVAL_SECONDS,
        ge=MIN_MONITOR_INTERVAL_SECONDS,
        le=MAX_MONITOR_INTERVAL_SECONDS,
    )
    fetch_depth: int = Field(
        default=DEFAULT_MONITOR_DEPTH,
        ge=MIN_MONITOR_DEPTH,
        le=MAX_MONITOR_DEPTH,
    )
    max_messages: int = Field(
        default=DEFAULT_MAX_MESSAGES,
        ge=MIN_MAX_MESSAGES,
        le=MAX_MAX_MESSAGES,
    )
    ai_prompt: str | None = None


class ClearMessagesPayload(BaseModel):
    confirm: bool = False


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
