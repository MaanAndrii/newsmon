from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from config import (
    DEFAULT_MAX_MESSAGES,
    DEFAULT_MONITOR_DEPTH,
    MAX_MAX_MESSAGES,
    MAX_MONITOR_DEPTH,
    MAX_MONITOR_INTERVAL_SECONDS,
    MIN_MAX_MESSAGES,
    MIN_MONITOR_DEPTH,
    MIN_MONITOR_INTERVAL_SECONDS,
    MONITOR_INTERVAL_SECONDS,
    _ai_counters,
    ai_processing_lock,
    event_log,
    monitor_run_history,
    monitor_status,
    repo,
    telethon_client_lock,
)
from services.alerts import _process_alerts_for_message
from services.claude import (
    _call_claude_score_sync,
    _prepare_ai_text,
    _resolve_claude_model,
)
from services.telegram import _extract_telegram_username, _record_telegram_call
from services.telethon import _get_saved_string_session, _telethon_client_init_data, _telethon_session_base

# Maximum concurrent Telethon channel fetches per monitor cycle
_CHANNEL_CONCURRENCY = 3

# Maximum concurrent Claude API calls per AI queue flush
_AI_CONCURRENCY = 4

# How often the dedicated AI loop wakes up (seconds)
_AI_LOOP_INTERVAL = 30


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def _log_event(event_type: str, detail: str, **extra: object) -> None:
    """Append an entry to the in-memory event log (last 50 entries)."""
    event_log.append(
        {
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "type": event_type,
            "detail": detail,
            **extra,
        }
    )


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_default_category_name() -> str:
    categories = repo.list_categories()
    default_item = next((c for c in categories if c.get("is_default")), None)
    if default_item and default_item.get("name"):
        return str(default_item["name"])
    return "Без категорії"


def _detect_media_type(message: object) -> str | None:
    media = getattr(message, "media", None)
    if not media:
        return None
    media_name = media.__class__.__name__.lower()
    if "photo" in media_name:
        return "photo"
    if "document" in media_name:
        return "document"
    return "media"


def _get_monitor_config() -> dict[str, bool | int | str]:
    collect_enabled = (repo.get_setting("monitor.collect_enabled", "1") or "1") == "1"
    ai_enabled = (repo.get_setting("monitor.ai_enabled", "1") or "1") == "1"
    interval_raw = (
        repo.get_setting("monitor.interval_seconds", str(MONITOR_INTERVAL_SECONDS))
        or str(MONITOR_INTERVAL_SECONDS)
    )
    try:
        interval_seconds = int(interval_raw)
    except ValueError:
        interval_seconds = MONITOR_INTERVAL_SECONDS
    interval_seconds = max(
        MIN_MONITOR_INTERVAL_SECONDS, min(MAX_MONITOR_INTERVAL_SECONDS, interval_seconds)
    )
    depth_raw = (
        repo.get_setting("monitor.fetch_depth", str(DEFAULT_MONITOR_DEPTH))
        or str(DEFAULT_MONITOR_DEPTH)
    )
    try:
        fetch_depth = int(depth_raw)
    except ValueError:
        fetch_depth = DEFAULT_MONITOR_DEPTH
    fetch_depth = max(MIN_MONITOR_DEPTH, min(MAX_MONITOR_DEPTH, fetch_depth))
    max_messages_raw = (
        repo.get_setting("monitor.max_messages", str(DEFAULT_MAX_MESSAGES))
        or str(DEFAULT_MAX_MESSAGES)
    )
    try:
        max_messages = int(max_messages_raw)
    except ValueError:
        max_messages = DEFAULT_MAX_MESSAGES
    max_messages = max(MIN_MAX_MESSAGES, min(MAX_MAX_MESSAGES, max_messages))
    ai_prompt = (repo.get_setting("monitor.ai_prompt", "") or "").strip()
    return {
        "collect_enabled": collect_enabled,
        "ai_enabled": ai_enabled,
        "interval_seconds": interval_seconds,
        "fetch_depth": fetch_depth,
        "max_messages": max_messages,
        "ai_prompt": ai_prompt,
    }


# ---------------------------------------------------------------------------
# Per-source message fetcher (runs concurrently via asyncio.gather)
# ---------------------------------------------------------------------------

async def _fetch_one_source(
    client: object,
    source: dict,
    semaphore: asyncio.Semaphore,
    window_start: datetime,
    fetch_depth: int,
    ai_enabled: bool,
) -> tuple[int, int]:
    """Fetch new messages for a single source.

    Returns (updated, ingested) counts.  Errors are logged and silenced so
    one bad channel doesn't block the others.
    """
    username = _extract_telegram_username(source["url"] or "")
    if not username:
        return 0, 0

    async with semaphore:
        try:
            from telethon.tl.types import InputPeerChannel
        except ImportError:
            return 0, 0

        try:
            target: object | None = None
            cached_peer_id = source.get("tg_peer_id")
            cached_access_hash = source.get("tg_access_hash")
            if cached_peer_id and cached_access_hash is not None:
                try:
                    target = InputPeerChannel(int(cached_peer_id), int(cached_access_hash))
                except Exception:
                    target = None
            if target is None:
                entity = await client.get_entity(username)
                _record_telegram_call()
                try:
                    channel_id = int(getattr(entity, "id", 0) or 0)
                    channel_hash = int(getattr(entity, "access_hash", 0) or 0)
                except (TypeError, ValueError):
                    channel_id = 0
                    channel_hash = 0
                if channel_id and channel_hash:
                    repo.update_source_tg_peer(int(source["id"]), channel_id, channel_hash)
                    target = InputPeerChannel(channel_id, channel_hash)
                else:
                    target = entity

            last_known_id = repo.get_last_tg_message_id(int(source["id"]))
            candidates: list[object] = []
            latest_dt_utc: datetime | None = None
            async for message in client.iter_messages(target, limit=fetch_depth):
                if not message:
                    continue
                message_id = int(getattr(message, "id", 0))
                msg_date = getattr(message, "date", None)
                if message_id <= 0 or msg_date is None:
                    continue
                msg_date_utc = msg_date.astimezone(timezone.utc)
                if latest_dt_utc is None:
                    latest_dt_utc = msg_date_utc
                if message_id <= last_known_id:
                    continue
                if msg_date_utc < window_start:
                    continue
                # Skip extra media-group items (album photos/videos without caption).
                # In Telegram albums the first item carries the text; the rest share
                # grouped_id but have no text — useless for the dashboard.
                msg_text = (
                    getattr(message, "message", None)
                    or getattr(message, "raw_text", None)
                    or getattr(message, "text", None)
                    or ""
                )
                if getattr(message, "grouped_id", None) is not None and not msg_text.strip():
                    continue
                candidates.append(message)
            _record_telegram_call()

            src_updated = 0
            if latest_dt_utc is not None:
                repo.update_source_last_message(
                    int(source["id"]),
                    latest_dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                )
                src_updated = 1

            src_ingested = 0
            for message in reversed(candidates):
                message_id = int(getattr(message, "id", 0))
                msg_date = getattr(message, "date", None)
                if message_id <= 0 or msg_date is None:
                    continue
                msg_date_utc = msg_date.astimezone(timezone.utc)
                text = (
                    getattr(message, "message", None)
                    or getattr(message, "raw_text", None)
                    or getattr(message, "text", None)
                    or ""
                )
                # Only send to AI queue when source has AI enabled AND the
                # message actually contains text — media-only posts are
                # marked done immediately to avoid an infinite error loop.
                has_text = bool(text.strip())
                should_ai = ai_enabled and bool(source.get("ai_enabled")) and has_text
                new_message_id = repo.upsert_message(
                    source_id=int(source["id"]),
                    tg_message_id=message_id,
                    published_at=msg_date_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    text=text,
                    media_type=_detect_media_type(message),
                    telegram_url=f"https://t.me/{username}/{message_id}",
                    raw_json=json.dumps(
                        message.to_dict(),
                        ensure_ascii=False,
                        default=str,
                    ),
                    enqueue_ai=should_ai,
                )
                if not should_ai:
                    repo.mark_message_no_ai(new_message_id, _get_default_category_name())
                src_ingested += 1
                await _process_alerts_for_message(new_message_id, "new_message")

            if src_ingested > 0:
                _log_event(
                    "source_ok",
                    f"@{username}: +{src_ingested} повідомлень",
                    username=username,
                    ingested=src_ingested,
                )
            return src_updated, src_ingested

        except Exception as exc:
            _log_event(
                "source_error",
                f"@{username}: {type(exc).__name__}: {str(exc)[:200]}",
                username=username,
            )
            return 0, 0


async def _sync_sources_last_messages() -> tuple[int, int, int, str | None]:
    monitor_cfg = _get_monitor_config()
    if not monitor_cfg["collect_enabled"]:
        return 0, 0, 0, "Збір повідомлень глобально вимкнений у вкладці Моніторинг"
    fetch_depth = int(monitor_cfg["fetch_depth"])

    integrations = repo.get_integrations()
    api_id = (integrations.get("telegram_api_id") or "").strip()
    api_hash = (integrations.get("telegram_api_hash") or "").strip()
    if not re.fullmatch(r"\d{5,12}", api_id) or not re.fullmatch(
        r"[a-fA-F0-9]{32}", api_hash
    ):
        return 0, 0, 0, "Telegram User API ID/Hash не заповнені або некоректні"

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        return (
            0,
            0,
            0,
            "Telethon не встановлено (виконайте: pip install -r backend/requirements.txt)",
        )

    sources = repo.list_sources(sort_by="alpha")
    session_path = _telethon_session_base()
    window_start = datetime.now(timezone.utc) - timedelta(
        seconds=int(monitor_cfg["interval_seconds"])
    )

    async with telethon_client_lock:
        try:
            client_mode, parsed_api_id, parsed_api_hash = _telethon_client_init_data(
                int(api_id), api_hash
            )
            session_obj = (
                StringSession(_get_saved_string_session())
                if client_mode == "string"
                else str(session_path)
            )
            client = TelegramClient(session_obj, parsed_api_id, parsed_api_hash)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    _record_telegram_call()
                    return (
                        0,
                        len(sources),
                        0,
                        "Telethon-сесія не авторизована (потрібен login)",
                    )
                _record_telegram_call()

                active_sources = [
                    s for s in sources
                    if s.get("is_active") and _extract_telegram_username(s["url"] or "")
                ]
                semaphore = asyncio.Semaphore(_CHANNEL_CONCURRENCY)
                results = await asyncio.gather(
                    *[
                        _fetch_one_source(
                            client, src, semaphore, window_start,
                            fetch_depth, bool(monitor_cfg["ai_enabled"]),
                        )
                        for src in active_sources
                    ],
                    return_exceptions=True,
                )

                updated = 0
                ingested = 0
                for result in results:
                    if isinstance(result, tuple):
                        src_updated, src_ingested = result
                        updated += src_updated
                        ingested += src_ingested

                repo.enforce_max_messages(int(monitor_cfg["max_messages"]))
            finally:
                await client.disconnect()
        except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            return 0, len(sources), 0, f"Session DB помилка: {exc}"
    return updated, len(sources), ingested, None


# ---------------------------------------------------------------------------
# AI queue processing
# ---------------------------------------------------------------------------

async def _process_one_ai_item(
    item: dict,
    semaphore: asyncio.Semaphore,
    api_key: str,
    model: str,
    categories: list[str],
    ai_prompt: str,
    keyword_patterns: list[str],
) -> None:
    async with semaphore:
        message_id = int(item.get("message_id") or 0)
        text = (item.get("text") or "").strip()
        if message_id <= 0 or not text:
            # Mark as done without a score so it appears on the dashboard
            # and is never retried.
            repo.mark_message_no_ai(message_id, _get_default_category_name())
            _log_event("ai_flush", f"msg#{message_id}: порожній текст → позначено без оцінки")
            return
        try:
            loop = asyncio.get_running_loop()
            # Pass keyword_patterns so scoring and keyword matching happen in one API call.
            score, category, matched_keyword = await loop.run_in_executor(
                None,
                _call_claude_score_sync,
                api_key,
                model,
                _prepare_ai_text(text),
                categories,
                ai_prompt,
                keyword_patterns or None,
            )
            if category is None:
                category = _get_default_category_name()
            repo.mark_ai_result(message_id, score, category)
            _log_event(
                "ai_scored",
                f"msg#{message_id}: score={score}, cat={category}",
                message_id=message_id,
                score=score,
                category=category,
            )
            await _process_alerts_for_message(
                message_id, "ai_scored", score=score, matched_keyword=matched_keyword
            )
        except Exception as exc:
            repo.mark_ai_error(message_id, str(exc))
            _log_event(
                "ai_error",
                f"msg#{message_id}: {type(exc).__name__}: {str(exc)[:200]}",
                message_id=message_id,
            )


async def _process_ai_queue(limit: int = 50) -> int:
    """Process up to *limit* pending AI queue items.

    Returns the number of items that were claimed for processing.

    When AI is disabled or no API key is set, flushes pending items as
    no-AI-done so they immediately appear on the dashboard.
    """
    if ai_processing_lock.locked():
        return 0

    monitor_cfg = _get_monitor_config()

    if not monitor_cfg["ai_enabled"]:
        flushed = repo.flush_ai_queue_no_ai(_get_default_category_name())
        if flushed:
            _log_event(
                "ai_flush",
                f"AI вимкнено: {flushed} повідомлень позначено без оцінки",
                flushed=flushed,
            )
        return 0

    integrations = repo.get_integrations()
    api_key = (integrations.get("claude_api_key") or "").strip()

    if not api_key:
        flushed = repo.flush_ai_queue_no_ai(_get_default_category_name())
        if flushed:
            _log_event(
                "ai_flush",
                f"Немає Claude API ключа: {flushed} повідомлень позначено без оцінки",
                flushed=flushed,
            )
        return 0

    model = _resolve_claude_model(integrations.get("claude_model"))
    categories = [
        c.get("name", "").strip() for c in repo.list_categories() if c.get("name")
    ]
    ai_prompt = str(monitor_cfg.get("ai_prompt") or "").strip()
    # Collect all active keyword_ai patterns once per queue flush to avoid
    # per-message DB queries and to pass them into the combined scoring call.
    keyword_patterns: list[str] = list({
        str(a.get("pattern") or "").strip()
        for a in repo.list_alerts()
        if int(a.get("is_enabled") or 0) == 1
        and str(a.get("alert_type") or "") == "keyword_ai"
        and str(a.get("pattern") or "").strip()
    })

    async with ai_processing_lock:
        pending = repo.claim_ai_queue_pending(limit=limit)
        if not pending:
            return 0
        count = len(pending)
        semaphore = asyncio.Semaphore(_AI_CONCURRENCY)
        await asyncio.gather(
            *[
                _process_one_ai_item(
                    item, semaphore, api_key, model, categories, ai_prompt, keyword_patterns
                )
                for item in pending
            ],
            return_exceptions=True,
        )
        # Update shared counter so _monitor_loop can read it for run history
        _ai_counters["processed_since_last_collect"] += count
    return count


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

async def _monitor_loop() -> None:
    while True:
        try:
            monitor_cfg = _get_monitor_config()
            monitor_status["interval_seconds"] = int(monitor_cfg["interval_seconds"])
            monitor_status["state"] = "running"
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            monitor_status["last_run_at"] = now_str
            _log_event("collect_start", "Запуск циклу збору")

            updated, total, ingested, err = await _sync_sources_last_messages()
            monitor_status["updated_sources"] = updated
            monitor_status["total_sources"] = total
            monitor_status["ingested_messages"] = ingested

            # Snapshot and reset the AI counter that accumulated since last cycle
            ai_since_last = _ai_counters["processed_since_last_collect"]
            _ai_counters["processed_since_last_collect"] = 0

            if err:
                monitor_status["state"] = "warning"
                monitor_status["last_error"] = err
                _log_event(
                    "collect_warning", err,
                    updated=updated, total=total, ingested=ingested,
                )
                monitor_run_history.append(
                    {
                        "at": now_str,
                        "updated": updated,
                        "total": total,
                        "ingested": ingested,
                        "ai_processed": ai_since_last,
                        "state": "warning",
                        "error": err,
                    }
                )
            else:
                monitor_status["state"] = "ok"
                monitor_status["last_error"] = None
                monitor_status["last_success_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                _log_event(
                    "collect_done",
                    f"Оновлено: {updated}/{total} джерел, нових: {ingested}",
                    updated=updated, total=total, ingested=ingested,
                )
                monitor_run_history.append(
                    {
                        "at": now_str,
                        "updated": updated,
                        "total": total,
                        "ingested": ingested,
                        "ai_processed": ai_since_last,
                        "state": "ok",
                        "error": None,
                    }
                )
        except Exception as exc:
            monitor_status["state"] = "error"
            monitor_status["last_error"] = "Непередбачена помилка моніторингу"
            _log_event(
                "collect_error",
                f"Непередбачена помилка: {type(exc).__name__}: {str(exc)[:200]}",
            )
        await asyncio.sleep(int(_get_monitor_config()["interval_seconds"]))


async def _ai_loop() -> None:
    """Dedicated background AI processing loop.

    Runs every _AI_LOOP_INTERVAL seconds, independently of the collect loop,
    so AI scoring doesn't block or delay message ingestion.
    """
    await asyncio.sleep(_AI_LOOP_INTERVAL)
    while True:
        try:
            # Recover stuck/failed items before each flush attempt
            stale = repo.reset_stale_ai_processing(minutes=5)
            retried = repo.reset_error_items_for_retry(max_retries=3)
            if stale:
                _log_event("ai_reset_stale", f"Скинуто {stale} завислих задач AI")
            if retried:
                _log_event("ai_reset_retry", f"Відновлено {retried} помилкових задач для повтору")

            processed = await _process_ai_queue()
            if processed:
                _log_event(
                    "ai_cycle_done",
                    f"AI цикл: оброблено {processed} повідомлень",
                    processed=processed,
                )
        except Exception as exc:
            _log_event(
                "ai_cycle_error",
                f"Помилка AI циклу: {type(exc).__name__}: {str(exc)[:200]}",
            )
        await asyncio.sleep(_AI_LOOP_INTERVAL)
