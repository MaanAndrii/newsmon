from __future__ import annotations

import asyncio

from config import repo
from services.claude import _prepare_ai_text
from services.providers import get_provider
from services.telegram import _send_telegram_bot_message


def _get_monitor_provider():
    ai_provider = (repo.get_setting("monitor.ai_provider", "claude") or "claude").strip()
    ai_model = (repo.get_setting("monitor.ai_model", "") or "").strip() or None
    integrations = repo.get_integrations()
    return get_provider(ai_provider, integrations, model_override=ai_model)


async def _process_alerts_for_message(
    message_id: int,
    event_type: str,
    score: int | None = None,
    matched_keyword: str | None = None,
    keyword_checked: bool = False,
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

    # matched_keyword is pre-resolved by the combined scoring call in monitor.py
    # when keyword_checked=True.  Fallback: run keyword matching via the current
    # monitor AI provider when keywords were not checked during scoring.
    keyword_alerts = [a for a in alerts if a.get("alert_type") == "keyword_ai"]
    if not keyword_checked and matched_keyword is None and event_type == "ai_scored" and keyword_alerts:
        keyword_candidates = list(
            {
                str(a.get("pattern") or "").strip()
                for a in keyword_alerts
                if str(a.get("pattern") or "").strip()
            }
        )
        if keyword_candidates and text.strip():
            provider = _get_monitor_provider()
            if provider.has_credentials():
                loop = asyncio.get_running_loop()
                try:
                    matched_keyword = await loop.run_in_executor(
                        None,
                        provider.match_keywords,
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
