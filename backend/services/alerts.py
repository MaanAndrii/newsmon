from __future__ import annotations

import asyncio

from config import repo
from services.claude import (
    _call_claude_keyword_match_sync,
    _prepare_ai_text,
    _resolve_claude_model,
)
from services.telegram import _send_telegram_bot_message


async def _process_alerts_for_message(
    message_id: int,
    event_type: str,
    score: int | None = None,
    matched_keyword: str | None = None,
) -> None:
    integrations = repo.get_integrations()
    bot_token = (integrations.get("telegram_bot_token") or "").strip()
    if not bot_token:
        return
    message = repo.get_message_by_id(message_id)
    if not message:
        return
    alerts = [a for a in repo.list_alerts() if int(a.get("is_enabled") or 0) == 1]
    if not alerts:
        return
    source_id = int(message.get("source_id") or 0)
    text = str(message.get("text") or "")
    ai_category = str(message.get("ai_category") or "—")
    ai_score = int(score if score is not None else (message.get("ai_score") or 0))
    telegram_url = str(message.get("telegram_url") or "")
    published_at = str(message.get("published_at") or "")
    source_name = str(message.get("source_name") or "Канал")

    # matched_keyword is pre-resolved by the combined scoring call in monitor.py.
    # Only fall back to a separate Claude call when it wasn't provided (e.g.
    # direct calls from new_message events or legacy code paths).
    keyword_alerts = [a for a in alerts if a.get("alert_type") == "keyword_ai"]
    if matched_keyword is None and event_type == "ai_scored" and keyword_alerts:
        keyword_candidates = list(
            {
                str(a.get("pattern") or "").strip()
                for a in keyword_alerts
                if str(a.get("pattern") or "").strip()
            }
        )
        claude_key = (integrations.get("claude_api_key") or "").strip()
        model = _resolve_claude_model(integrations.get("claude_model"))
        if claude_key and keyword_candidates and text.strip():
            loop = asyncio.get_running_loop()
            try:
                matched_keyword = await loop.run_in_executor(
                    None,
                    _call_claude_keyword_match_sync,
                    claude_key,
                    model,
                    _prepare_ai_text(text),
                    keyword_candidates,
                )
            except Exception:
                matched_keyword = None

    for alert in alerts:
        alert_id = int(alert.get("id") or 0)
        if alert_id <= 0:
            continue
        if repo.is_alert_delivered(alert_id, message_id):
            continue
        alert_type = str(alert.get("alert_type") or "new_message")
        alert_source = int(alert.get("source_id") or 0)
        if alert_source and alert_source != source_id:
            continue
        should_send = False
        keyword_for_delivery: str | None = None
        if alert_type == "new_message" and event_type == "new_message":
            should_send = True
        elif alert_type == "min_score" and event_type == "ai_scored":
            min_score = int(alert.get("min_score") or 0)
            should_send = ai_score >= min_score
        elif alert_type == "keyword_ai" and event_type == "ai_scored":
            expected = str(alert.get("pattern") or "").strip()
            should_send = bool(
                matched_keyword
                and expected
                and matched_keyword.lower() == expected.lower()
            )
            keyword_for_delivery = matched_keyword
        if not should_send:
            continue
        target_chat_id = str(alert.get("target_chat_id") or "").strip()
        if not target_chat_id:
            continue
        alert_name = str(alert.get("name") or "Alert")
        msg = (
            f"🔔 {alert_name}\n"
            f"Канал: {source_name}\n"
            f"Час: {published_at}\n"
            f"Оцінка: {ai_score}\n"
            f"Категорія: {ai_category}\n"
            f"{'Ключове слово: ' + keyword_for_delivery + chr(10) if keyword_for_delivery else ''}"
            f"Текст: {(text or '—')[:800]}\n"
            f"{telegram_url}"
        )
        try:
            sent = _send_telegram_bot_message(bot_token, target_chat_id, msg)
        except Exception:
            continue
        if sent:
            repo.mark_alert_delivered(alert_id, message_id, keyword_for_delivery)
