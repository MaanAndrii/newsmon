from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from config import (
    DEFAULT_MONITOR_DEPTH,
    DEFAULT_RETENTION_MONTHS,
    MAX_MONITOR_DEPTH,
    MAX_MONITOR_INTERVAL_SECONDS,
    MAX_RETENTION_MONTHS,
    MIN_MONITOR_DEPTH,
    MIN_MONITOR_INTERVAL_SECONDS,
    MIN_RETENTION_MONTHS,
    MONITOR_INTERVAL_SECONDS,
    _ai_counters,
    ai_processing_lock,
    broadcast_sse,
    event_log,
    monitor_run_history,
    monitor_status,
    repo,
    telethon_client_lock,
)
from services.alerts import _process_alerts_for_message
from services.claude import _prepare_ai_text
from services.providers import get_provider
from services.providers.claude import ClaudeProvider
from services.providers.openai_compat import OpenAICompatProvider
from services.telegram import _extract_telegram_username, _record_telegram_call
from services.telethon import _get_saved_string_session, _telethon_client_init_data, _telethon_session_base

# Maximum concurrent Telethon channel fetches per monitor cycle
_CHANNEL_CONCURRENCY = 3

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _MONITOR_TZ = _ZoneInfo("Europe/Kyiv")
except Exception:
    _MONITOR_TZ = timezone.utc

# Per-source adaptive state: {source_id: {empty_streak, interval, skip_until}}
_source_adaptive: dict[int, dict] = {}

# Maximum concurrent Claude API calls per AI queue flush
_AI_CONCURRENCY = 4

# How often the dedicated AI loop wakes up (seconds)
_AI_LOOP_INTERVAL = 30


# ---------------------------------------------------------------------------
# Content-hash helpers for deduplication
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t, flags=re.UNICODE)
    return t


def _compute_content_hash(text: str) -> str | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    try:
        repo.log_event(event_type, detail)
    except Exception:
        pass


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


def _parse_schedule(raw: str) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            return []
        result = []
        for slot in data:
            if not isinstance(slot, dict):
                continue
            if not (slot.get("from") and slot.get("to") and slot.get("interval_seconds")):
                continue
            result.append({
                "from": str(slot["from"]),
                "to": str(slot["to"]),
                "interval_seconds": int(slot["interval_seconds"]),
            })
        return result
    except Exception:
        return []


def _get_current_interval(cfg: dict) -> int:
    """Return interval_seconds for current Kyiv local time based on schedule.

    Falls back to cfg["interval_seconds"] when no schedule slot matches.
    """
    schedule = cfg.get("schedule") or []
    fallback = int(cfg.get("interval_seconds", MONITOR_INTERVAL_SECONDS))
    if not schedule:
        return fallback

    now = datetime.now(timezone.utc).astimezone(_MONITOR_TZ)
    now_min = now.hour * 60 + now.minute

    for slot in schedule:
        try:
            fh, fm = map(int, str(slot["from"]).split(":"))
            th, tm = map(int, str(slot["to"]).split(":"))
            from_min = fh * 60 + fm
            to_min = th * 60 + tm
            slot_secs = int(slot["interval_seconds"])
        except (KeyError, ValueError, TypeError):
            continue
        in_slot = (
            (from_min <= now_min < to_min)
            if from_min < to_min
            else (now_min >= from_min or now_min < to_min)
        )
        if in_slot:
            return max(MIN_MONITOR_INTERVAL_SECONDS, min(MAX_MONITOR_INTERVAL_SECONDS, slot_secs))

    return fallback


def _update_source_adaptive(source_id: int, ingested: int, base_interval: int) -> None:
    """Update per-source adaptive state after a collection attempt."""
    state = _source_adaptive.setdefault(source_id, {
        "empty_streak": 0, "interval": base_interval, "skip_until": 0.0,
    })
    if ingested > 0:
        state["empty_streak"] = 0
        state["interval"] = base_interval
        state["skip_until"] = 0.0
    else:
        state["empty_streak"] = state.get("empty_streak", 0) + 1
        if state["empty_streak"] >= 2:
            new_interval = min(state.get("interval", base_interval) * 2, 7200)
            state["interval"] = new_interval
            state["skip_until"] = time.monotonic() + new_interval


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
    retention_raw = (
        repo.get_setting("monitor.retention_months", str(DEFAULT_RETENTION_MONTHS))
        or str(DEFAULT_RETENTION_MONTHS)
    )
    try:
        retention_months = int(retention_raw)
    except ValueError:
        retention_months = DEFAULT_RETENTION_MONTHS
    retention_months = max(
        MIN_RETENTION_MONTHS, min(MAX_RETENTION_MONTHS, retention_months)
    )
    ai_prompt = (repo.get_setting("monitor.ai_prompt", "") or "").strip()
    dedup_enabled = (repo.get_setting("monitor.dedup_enabled", "1") or "1") == "1"
    ai_provider = (repo.get_setting("monitor.ai_provider", "claude") or "claude").strip()
    ai_model = (repo.get_setting("monitor.ai_model", "") or "").strip()
    schedule = _parse_schedule(repo.get_setting("monitor.schedule", "[]") or "[]")
    adaptive_enabled = (repo.get_setting("monitor.adaptive_enabled", "0") or "0") == "1"
    return {
        "collect_enabled": collect_enabled,
        "ai_enabled": ai_enabled,
        "interval_seconds": interval_seconds,
        "fetch_depth": fetch_depth,
        "retention_months": retention_months,
        "ai_prompt": ai_prompt,
        "dedup_enabled": dedup_enabled,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "schedule": schedule,
        "adaptive_enabled": adaptive_enabled,
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
    dedup_enabled: bool = True,
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

                # Dedup check: if content hash already scored recently, copy result.
                content_hash = _compute_content_hash(text) if has_text else None
                dedup_original: dict | None = None
                if dedup_enabled and content_hash and should_ai:
                    dedup_original = repo.find_scored_message_by_hash(content_hash, hours=6)

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
                    enqueue_ai=should_ai and dedup_original is None,
                    content_hash=content_hash,
                )
                if dedup_original is not None:
                    repo.mark_message_dedup(
                        new_message_id,
                        int(dedup_original["ai_score"]),
                        dedup_original.get("ai_category"),
                    )
                    _log_event(
                        "dedup_hit",
                        f"@{username} msg#{message_id}: дублікат msg#{dedup_original['id']}",
                        username=username,
                        message_id=new_message_id,
                        original_id=dedup_original["id"],
                    )
                elif not should_ai:
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
    current_interval = _get_current_interval(monitor_cfg)
    window_start = datetime.now(timezone.utc) - timedelta(seconds=current_interval)

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

                all_active = [
                    s for s in sources
                    if s.get("is_active") and _extract_telegram_username(s["url"] or "")
                ]
                # Adaptive: skip sources whose backoff window hasn't expired
                adaptive_on = bool(monitor_cfg.get("adaptive_enabled"))
                now_mono = time.monotonic()
                active_sources = [
                    s for s in all_active
                    if not adaptive_on
                    or now_mono >= _source_adaptive.get(int(s["id"]), {}).get("skip_until", 0.0)
                ]

                semaphore = asyncio.Semaphore(_CHANNEL_CONCURRENCY)
                results = await asyncio.gather(
                    *[
                        _fetch_one_source(
                            client, src, semaphore, window_start,
                            fetch_depth, bool(monitor_cfg["ai_enabled"]),
                            bool(monitor_cfg["dedup_enabled"]),
                        )
                        for src in active_sources
                    ],
                    return_exceptions=True,
                )

                updated = 0
                ingested = 0
                for src, result in zip(active_sources, results):
                    if isinstance(result, tuple):
                        src_updated, src_ingested = result
                        updated += src_updated
                        ingested += src_ingested
                        if adaptive_on:
                            _update_source_adaptive(int(src["id"]), src_ingested, current_interval)

                repo.enforce_retention_months(int(monitor_cfg["retention_months"]))
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
    provider: ClaudeProvider | OpenAICompatProvider,
    categories: list[str],
    ai_prompt: str,
    keyword_patterns: list[str],
    dedup_enabled: bool = True,
    ai_provider_name: str = "claude",
) -> None:
    async with semaphore:
        message_id = int(item.get("message_id") or 0)
        text = (item.get("text") or "").strip()
        if message_id <= 0 or not text:
            repo.mark_message_no_ai(message_id, _get_default_category_name())
            _log_event("ai_flush", f"msg#{message_id}: порожній текст → позначено без оцінки")
            return

        # Re-check for a scored duplicate (handles race conditions between parallel fetches).
        if dedup_enabled:
            content_hash = _compute_content_hash(text)
            if content_hash:
                original = repo.find_scored_message_by_hash(content_hash, hours=6, exclude_id=message_id)
                if original:
                    repo.mark_message_dedup(message_id, int(original["ai_score"]), original.get("ai_category"))
                    _log_event(
                        "dedup_hit",
                        f"msg#{message_id}: дублікат (queue) msg#{original['id']}",
                        message_id=message_id,
                        original_id=original["id"],
                    )
                    return

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                provider.score_message,
                _prepare_ai_text(text),
                categories,
                ai_prompt,
                keyword_patterns or None,
            )
            score, category, matched_keyword = result.score, result.category, result.matched_keyword
            tok_in, tok_out = result.tokens_in, result.tokens_out
            if category is None:
                category = _get_default_category_name()
            repo.mark_ai_result(message_id, score, category)
            _log_event(
                "ai_scored",
                f"msg#{message_id}: score={score}, cat={category} [{ai_provider_name}, {tok_in}+{tok_out}tok]",
                message_id=message_id,
                score=score,
                category=category,
                provider=ai_provider_name,
                tokens_in=tok_in,
                tokens_out=tok_out,
            )
            await _process_alerts_for_message(
                message_id, "ai_scored", score=score,
                matched_keyword=matched_keyword,
                keyword_checked=bool(keyword_patterns),
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
    ai_provider_name = str(monitor_cfg.get("ai_provider") or "claude")
    ai_model_override = (monitor_cfg.get("ai_model") or "").strip() or None
    provider = get_provider(ai_provider_name, integrations, model_override=ai_model_override)

    if not provider.has_credentials():
        flushed = repo.flush_ai_queue_no_ai(_get_default_category_name())
        if flushed:
            _log_event(
                "ai_flush",
                f"Немає API ключа ({ai_provider_name}): {flushed} повідомлень позначено без оцінки",
                flushed=flushed,
            )
        return 0

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

        # Pre-dedup within this batch: if several messages share the same
        # content_hash, only the first occurrence goes through Claude. The rest
        # are held back, scored after the primaries finish, and marked as dedup.
        dedup_batch_enabled = bool(monitor_cfg["dedup_enabled"])
        primary_items = pending
        dedup_items: list[dict] = []
        if dedup_batch_enabled:
            seen_hashes: dict[str, bool] = {}
            primary_items = []
            for item in pending:
                text = (item.get("text") or "").strip()
                h = _compute_content_hash(text) if text else None
                if h and h in seen_hashes:
                    dedup_items.append(item)
                else:
                    primary_items.append(item)
                    if h:
                        seen_hashes[h] = True

        semaphore = asyncio.Semaphore(_AI_CONCURRENCY)
        await asyncio.gather(
            *[
                _process_one_ai_item(
                    item, semaphore, provider, categories, ai_prompt, keyword_patterns,
                    dedup_batch_enabled, ai_provider_name,
                )
                for item in primary_items
            ],
            return_exceptions=True,
        )

        # Resolve within-batch duplicates now that primaries are scored.
        for item in dedup_items:
            msg_id = int(item.get("message_id") or 0)
            text = (item.get("text") or "").strip()
            if not text or msg_id <= 0:
                repo.mark_message_no_ai(msg_id, _get_default_category_name())
                continue
            h = _compute_content_hash(text)
            original = repo.find_scored_message_by_hash(h, hours=6, exclude_id=msg_id) if h else None
            if original:
                repo.mark_message_dedup(msg_id, int(original["ai_score"]), original.get("ai_category"))
                _log_event(
                    "dedup_hit",
                    f"msg#{msg_id}: дублікат (batch) msg#{original['id']}",
                    message_id=msg_id,
                    original_id=original["id"],
                )
            else:
                # Fallback: original wasn't scored (e.g. AI error) — score normally.
                await _process_one_ai_item(
                    item, semaphore, provider, categories, ai_prompt, keyword_patterns,
                    dedup_batch_enabled,
                )

        # Update shared counter so _monitor_loop can read it for run history
        _ai_counters["processed_since_last_collect"] += count
        if count:
            broadcast_sse("messages_updated", {"scored": count})
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
                try:
                    repo.log_run(now_str, updated, total, ingested, ai_since_last, "warning", err)
                except Exception:
                    pass
                broadcast_sse("monitor_status", {k: v for k, v in monitor_status.items() if k != "last_error"})
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
                try:
                    repo.log_run(now_str, updated, total, ingested, ai_since_last, "ok", None)
                except Exception:
                    pass
                broadcast_sse("monitor_status", {k: v for k, v in monitor_status.items() if k != "last_error"})
        except Exception as exc:
            monitor_status["state"] = "error"
            monitor_status["last_error"] = "Непередбачена помилка моніторингу"
            _log_event(
                "collect_error",
                f"Непередбачена помилка: {type(exc).__name__}: {str(exc)[:200]}",
            )
        try:
            interval = _get_current_interval(_get_monitor_config())
        except Exception:
            interval = MONITOR_INTERVAL_SECONDS
        # Sleep in 10-second chunks so interval/schedule changes take effect within ~10s
        target = time.monotonic() + _seconds_until_next_tick(interval)
        while True:
            remaining = target - time.monotonic()
            if remaining <= 1.0:
                break
            try:
                new_interval = _get_current_interval(_get_monitor_config())
            except Exception:
                new_interval = interval
            if new_interval != interval:
                interval = new_interval
                target = time.monotonic() + _seconds_until_next_tick(interval)
            await asyncio.sleep(min(10.0, max(1.0, target - time.monotonic())))


def _seconds_until_next_tick(interval: int) -> float:
    """Return seconds to sleep until the next grid-aligned tick.

    Ticks are anchored to 00:00:00 UTC and repeat every *interval* seconds.
    Example: interval=1200 (20 min) → ticks at 00:00, 00:20, 00:40, 01:00 …
    The first tick after startup may be less than a full interval away.
    Minimum sleep is 1 second to avoid busy-looping on clock edge.
    """
    now = datetime.now(timezone.utc)
    seconds_since_midnight = (
        now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1_000_000
    )
    remainder = seconds_since_midnight % interval
    wait = interval - remainder if remainder > 0 else interval
    return max(1.0, wait)


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
