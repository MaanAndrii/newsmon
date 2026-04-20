from __future__ import annotations

import re

from fastapi import APIRouter, Depends

from config import repo
from models import IntegrationsPayload
from security import _mask_secret, require_admin
from services.claude import _resolve_claude_model

router = APIRouter()

SECRET_INTEGRATION_FIELDS = (
    "claude_api_key",
    "grok_api_key",
    "gemini_api_key",
    "telegram_api_hash",
    "telegram_bot_token",
)
PUBLIC_INTEGRATION_FIELDS = ("telegram_api_id",)


def _integrations_public_view(data: dict) -> dict:
    result: dict[str, object] = {}
    for key in SECRET_INTEGRATION_FIELDS:
        raw = (data.get(key) or "").strip()
        result[f"{key}_set"] = bool(raw)
        result[f"{key}_preview"] = _mask_secret(raw)
    for key in PUBLIC_INTEGRATION_FIELDS:
        result[key] = (data.get(key) or "").strip()
    result["claude_model"] = _resolve_claude_model(data.get("claude_model"))
    result["grok_model"] = (data.get("grok_model") or "").strip()
    result["gemini_model"] = (data.get("gemini_model") or "").strip()
    return result


@router.get("/api/integrations", dependencies=[Depends(require_admin)])
def get_integrations() -> dict:
    return _integrations_public_view(repo.get_integrations())


@router.post("/api/integrations", dependencies=[Depends(require_admin)])
def save_integrations(payload: IntegrationsPayload) -> dict:
    incoming = payload.model_dump()
    existing = repo.get_integrations()
    merged: dict[str, object] = {}
    for key in SECRET_INTEGRATION_FIELDS:
        new_value = (
            (incoming.get(key) or "").strip()
            if isinstance(incoming.get(key), str)
            else ""
        )
        merged[key] = new_value or (existing.get(key) or "").strip()
    for key in PUBLIC_INTEGRATION_FIELDS:
        new_value = (
            (incoming.get(key) or "").strip()
            if isinstance(incoming.get(key), str)
            else ""
        )
        merged[key] = new_value or (existing.get(key) or "").strip()
    merged["claude_model"] = _resolve_claude_model(incoming.get("claude_model"))
    merged["grok_model"] = (incoming.get("grok_model") or "").strip()
    merged["gemini_model"] = (incoming.get("gemini_model") or "").strip()
    saved = repo.save_integrations(merged)
    return _integrations_public_view(saved)


@router.post("/api/integrations/validate", dependencies=[Depends(require_admin)])
def validate_integrations(payload: IntegrationsPayload) -> dict:
    data = payload.model_dump()
    existing = repo.get_integrations()

    def _pick(key: str) -> str:
        raw = data.get(key)
        raw_str = (raw or "").strip() if isinstance(raw, str) else ""
        return raw_str or (existing.get(key) or "").strip()

    claude_key = _pick("claude_api_key")
    claude_model = _resolve_claude_model(
        data.get("claude_model") or existing.get("claude_model")
    )
    telegram_api_id = _pick("telegram_api_id")
    telegram_api_hash = _pick("telegram_api_hash")
    telegram_bot_token = _pick("telegram_bot_token")

    claude_format = bool(
        re.fullmatch(r"sk-ant-(?:api03-)?[A-Za-z0-9_-]{20,}", claude_key)
    )
    telegram_user_format = bool(
        re.fullmatch(r"\d{5,12}", telegram_api_id)
        and re.fullmatch(r"[a-fA-F0-9]{32}", telegram_api_hash)
    )
    telegram_bot_format = bool(
        re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", telegram_bot_token)
    )
    claude_model_ok = bool(
        re.fullmatch(r"claude-[a-z0-9]+(?:-[a-z0-9]+)+", claude_model or "")
    )
    claude_ok = claude_format and claude_model_ok
    claude_reason = (
        None if claude_ok
        else "Очікується ключ формату sk-ant-... і Model ID формату claude-*"
    )
    telegram_user_reason = (
        "API ID/Hash коректні. Фактична перевірка виконується через Telethon авторизацію."
        if telegram_user_format
        else "API ID має бути числом, API Hash — 32 hex-символи"
    )
    telegram_bot_ok = telegram_bot_format
    telegram_bot_reason = (
        None if telegram_bot_ok else "Bot token не відповідає формату Telegram"
    )
    return {
        "claude": {
            "ok": claude_ok,
            "reason": claude_reason,
            "model": claude_model,
        },
        "telegram_user_api": {
            "ok": telegram_user_format,
            "reason": telegram_user_reason,
        },
        "telethon": {
            "ok": telegram_user_format,
            "reason": None
            if telegram_user_format
            else "Для Telethon потрібні коректні Telegram API ID/Hash",
        },
        "telegram_bot_api": {
            "ok": telegram_bot_ok,
            "reason": telegram_bot_reason,
        },
        "overall_ok": claude_ok and telegram_user_format and telegram_bot_ok,
    }
