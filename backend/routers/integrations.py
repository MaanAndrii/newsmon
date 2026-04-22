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
BOOL_INTEGRATION_FIELDS = ("telegram_unknown_forward_enabled",)
TEXT_INTEGRATION_FIELDS = (
    "telegram_unknown_forward_primary",
    "telegram_unknown_forward_reserve",
)

_EXTRA_MODEL_FIELDS = (
    "claude_model_2", "claude_model_3",
    "grok_model_2", "grok_model_3",
    "gemini_model_2", "gemini_model_3",
)


def _integrations_public_view(data: dict) -> dict:
    result: dict[str, object] = {}
    for key in SECRET_INTEGRATION_FIELDS:
        raw = (data.get(key) or "").strip()
        result[f"{key}_set"] = bool(raw)
        result[f"{key}_preview"] = _mask_secret(raw)
    for key in PUBLIC_INTEGRATION_FIELDS:
        result[key] = (data.get(key) or "").strip()
    for key in BOOL_INTEGRATION_FIELDS:
        result[key] = bool(int(data.get(key) or 0))
    for key in TEXT_INTEGRATION_FIELDS:
        result[key] = (data.get(key) or "").strip()
    result["claude_model"] = _resolve_claude_model(data.get("claude_model"))
    result["grok_model"] = (data.get("grok_model") or "").strip()
    result["gemini_model"] = (data.get("gemini_model") or "").strip()
    for key in _EXTRA_MODEL_FIELDS:
        result[key] = (data.get(key) or "").strip()
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
    for key in BOOL_INTEGRATION_FIELDS:
        incoming_value = incoming.get(key)
        if incoming_value is None:
            merged[key] = bool(int(existing.get(key) or 0))
        else:
            merged[key] = bool(incoming_value)
    for key in TEXT_INTEGRATION_FIELDS:
        new_value = (
            (incoming.get(key) or "").strip()
            if isinstance(incoming.get(key), str)
            else ""
        )
        merged[key] = new_value or (existing.get(key) or "").strip()
    merged["claude_model"] = _resolve_claude_model(incoming.get("claude_model"))
    merged["grok_model"] = (incoming.get("grok_model") or "").strip()
    merged["gemini_model"] = (incoming.get("gemini_model") or "").strip()
    for key in _EXTRA_MODEL_FIELDS:
        merged[key] = (incoming.get(key) or "").strip()
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
    grok_key = _pick("grok_api_key")
    grok_model = (data.get("grok_model") or existing.get("grok_model") or "").strip()
    gemini_key = _pick("gemini_api_key")
    gemini_model = (data.get("gemini_model") or existing.get("gemini_model") or "").strip()
    telegram_api_id = _pick("telegram_api_id")
    telegram_api_hash = _pick("telegram_api_hash")
    telegram_bot_token = _pick("telegram_bot_token")
    unknown_forward_primary = _pick("telegram_unknown_forward_primary")
    unknown_forward_reserve = _pick("telegram_unknown_forward_reserve")

    # --- Claude ---
    claude_key_format = bool(
        re.fullmatch(r"sk-ant-(?:api03-)?[A-Za-z0-9_-]{20,}", claude_key)
    )
    claude_model_ok = bool(
        re.fullmatch(r"claude-[a-z0-9]+(?:-[a-z0-9]+)+", claude_model or "")
    )
    claude_ok = claude_key_format and claude_model_ok
    claude_reason = (
        None if claude_ok
        else "Очікується ключ формату sk-ant-... і Model ID формату claude-*"
    )

    # --- Grok ---
    grok_key_present = bool(grok_key)
    grok_model_present = bool(grok_model)
    if not grok_key_present:
        grok_ok = None
        grok_reason = "API ключ Grok не налаштовано"
    elif not grok_model_present:
        grok_ok = False
        grok_reason = "Model ID Grok не вказано"
    else:
        grok_ok = True
        grok_reason = f"Ключ присутній, модель: {grok_model}"

    # --- Gemini ---
    gemini_key_present = bool(gemini_key)
    gemini_model_present = bool(gemini_model)
    if not gemini_key_present:
        gemini_ok = None
        gemini_reason = "API ключ Gemini не налаштовано"
    elif not gemini_model_present:
        gemini_ok = False
        gemini_reason = "Model ID Gemini не вказано"
    else:
        gemini_ok = True
        gemini_reason = f"Ключ присутній, модель: {gemini_model}"

    # Extra models format check
    extra_model_issues: list[str] = []
    for field, prefix, pattern in [
        ("claude_model_2", "claude_model_2", r"claude-[a-z0-9]+(?:-[a-z0-9]+)+"),
        ("claude_model_3", "claude_model_3", r"claude-[a-z0-9]+(?:-[a-z0-9]+)+"),
    ]:
        val = _pick(field).strip()
        if val and not re.fullmatch(pattern, val):
            extra_model_issues.append(f"{field}: невірний формат")

    # --- Telegram ---
    telegram_user_format = bool(
        re.fullmatch(r"\d{5,12}", telegram_api_id)
        and re.fullmatch(r"[a-fA-F0-9]{32}", telegram_api_hash)
    )
    telegram_bot_format = bool(
        re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", telegram_bot_token)
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

    # Unknown forward destinations: allow @username, numeric id, +phone
    destination_pattern = r"(@[A-Za-z0-9_]{5,32}|-?\d{6,20}|\+\d{10,15})"
    unknown_primary_ok = (
        True if not unknown_forward_primary
        else bool(re.fullmatch(destination_pattern, unknown_forward_primary))
    )
    unknown_reserve_ok = (
        True if not unknown_forward_reserve
        else bool(re.fullmatch(destination_pattern, unknown_forward_reserve))
    )
    unknown_forward_ok = unknown_primary_ok and unknown_reserve_ok
    unknown_forward_reason = None
    if not unknown_forward_ok:
        unknown_forward_reason = (
            "Адреси пересилання мають бути у форматі @username, chat id або +телефон"
        )

    overall_ok = (
        claude_ok
        and telegram_user_format
        and telegram_bot_ok
        and unknown_forward_ok
        and not extra_model_issues
    )

    return {
        "claude": {
            "ok": claude_ok,
            "reason": claude_reason,
            "model": claude_model,
        },
        "grok": {
            "ok": grok_ok,
            "reason": grok_reason,
            "model": grok_model,
        },
        "gemini": {
            "ok": gemini_ok,
            "reason": gemini_reason,
            "model": gemini_model,
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
        "telegram_unknown_forward": {
            "ok": unknown_forward_ok,
            "reason": unknown_forward_reason,
        },
        "extra_model_issues": extra_model_issues,
        "overall_ok": overall_ok,
    }
