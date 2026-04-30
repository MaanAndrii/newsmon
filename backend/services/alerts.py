from __future__ import annotations

import asyncio

from config import repo
from services.lemmatizer import lemmatize, lemmas_from_json, match as lemma_match
from services.telegram import _send_telegram_bot_message


async def _process_alerts_for_message(
    message_id: int,
    event_type: str,
    score: int | None = None,
) -> None:
    bot_token = (repo.get_integrations().get("telegram_bot_token") or "").strip()
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

    # Pre-compute message lemmas once for all keyword_ai alerts
    text_lemmas = lemmatize(text) if event_type == "ai_scored" else frozenset()

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
            keyword_lemmas = lemmas_from_json(alert.get("keyword_lemmas"))
            should_send = lemma_match(text_lemmas, keyword_lemmas)
            if should_send:
                keyword_for_delivery = str(alert.get("pattern") or "").strip()
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
