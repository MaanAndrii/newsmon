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
    ai_processing_lock,
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

    Returns (updated, ingested) counts.  Never raises — errors are silently
    swallowed so one bad channel doesn't block the others.
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
                    enqueue_ai=ai_enabled and bool(source.get("ai_enabled")),
                )
                if not (ai_enabled and bool(source.get("ai_enabled"))):
                    repo.mark_message_no_ai(new_message_id, _get_default_category_name())
                src_ingested += 1
                await _process_alerts_for_message(new_message_id, "new_message")

            return src_updated, src_ingested
        except Exception:
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
# AI queue processing helpers
# ---------------------------------------------------------------------------

async def _process_one_ai_item(
    item: dict,
    semaphore: asyncio.Semaphore,
    api_key: str,
    model: str,
    categories: list[str],
    ai_prompt: str,
) -> None:
    async with semaphore:
        message_id = int(item.get("message_id") or 0)
        text = (item.get("text") or "").strip()
        if message_id <= 0 or not text:
            repo.mark_ai_error(message_id, "empty message text")
            return
        try:
            loop = asyncio.get_running_loop()
            score, category = await loop.run_in_executor(
                None,
                _call_claude_score_sync,
                api_key,
                model,
                _prepare_ai_text(text),
                categories,
                ai_prompt,
            )
            if category is None:
                category = _get_default_category_name()
            repo.mark_ai_result(message_id, score, category)
            await _process_alerts_for_message(message_id, "ai_scored", score=score)
        except Exception as exc:
            repo.mark_ai_error(message_id, str(exc))


async def _process_ai_queue(limit: int = 50) -> None:
    if ai_processing_lock.locked():
        return
    monitor_cfg = _get_monitor_config()
    if not monitor_cfg["ai_enabled"]:
        return
    integrations = repo.get_integrations()
    api_key = (integrations.get("claude_api_key") or "").strip()
    if not api_key:
        return
    model = _resolve_claude_model(integrations.get("claude_model"))
    categories = [
        c.get("name", "").strip() for c in repo.list_categories() if c.get("name")
    ]
    ai_prompt = str(monitor_cfg.get("ai_prompt") or "").strip()

    async with ai_processing_lock:
        pending = repo.claim_ai_queue_pending(limit=limit)
        if not pending:
            return
        semaphore = asyncio.Semaphore(_AI_CONCURRENCY)
        await asyncio.gather(
            *[
                _process_one_ai_item(
                    item, semaphore, api_key, model, categories, ai_prompt
                )
                for item in pending
            ],
            return_exceptions=True,
        )


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

async def _monitor_loop() -> None:
    while True:
        try:
            monitor_cfg = _get_monitor_config()
            monitor_status["interval_seconds"] = int(monitor_cfg["interval_seconds"])
            monitor_status["state"] = "running"
            monitor_status["last_run_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            updated, total, ingested, err = await _sync_sources_last_messages()
            monitor_status["updated_sources"] = updated
            monitor_status["total_sources"] = total
            monitor_status["ingested_messages"] = ingested
            if err:
                monitor_status["state"] = "warning"
                monitor_status["last_error"] = err
            else:
                monitor_status["state"] = "ok"
                monitor_status["last_error"] = None
                monitor_status["last_success_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
        except Exception:
            monitor_status["state"] = "error"
            monitor_status["last_error"] = "Непередбачена помилка моніторингу"
        await asyncio.sleep(int(_get_monitor_config()["interval_seconds"]))


async def _ai_loop() -> None:
    """Dedicated background AI processing loop.

    Runs every _AI_LOOP_INTERVAL seconds, independently of the collect loop,
    so AI scoring doesn't block or delay message ingestion.
    """
    await asyncio.sleep(_AI_LOOP_INTERVAL)
    while True:
        try:
            await _process_ai_queue()
        except Exception:
            pass
        await asyncio.sleep(_AI_LOOP_INTERVAL)
