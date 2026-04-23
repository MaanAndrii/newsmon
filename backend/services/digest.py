from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from config import broadcast_sse, repo
from services.providers import get_provider

try:
    from zoneinfo import ZoneInfo
    _KYIV_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    _KYIV_TZ = timezone.utc


def _get_digest_config() -> dict:
    def g(k: str, d: str) -> str:
        return repo.get_setting(f"digest.{k}", d) or d

    raw_excl = g("excluded_categories", "[]")
    try:
        excluded = json.loads(raw_excl)
    except Exception:
        excluded = []

    return {
        "enabled": g("enabled", "0") == "1",
        "hour": int(g("hour", "10")),
        "minute": int(g("minute", "0")),
        "min_score": int(g("min_score", "6")),
        "max_per_category": int(g("max_per_category", "5")),
        "excluded_categories": excluded,
        "ai_prompt": g("ai_prompt", ""),
        "keep_days": int(g("keep_days", "30")),
        "ai_provider": g("ai_provider", "claude"),
        "ai_model": g("ai_model", ""),
        "mode": g("mode", "previous_day"),
    }


async def _generate_daily_digest(reference_dt: datetime | None = None) -> dict:
    cfg = _get_digest_config()
    integrations = repo.get_integrations()

    ai_model_override = (cfg.get("ai_model") or "").strip() or None
    provider = get_provider(cfg["ai_provider"], integrations, model_override=ai_model_override)
    if not provider.has_credentials():
        return {"ok": False, "error": f"API ключ або модель для провайдера '{cfg['ai_provider']}' не налаштовані"}

    mode = cfg.get("mode", "previous_day")
    tz = _KYIV_TZ

    if reference_dt is None:
        reference_dt = datetime.now(timezone.utc).astimezone(tz)

    if mode == "previous_24h":
        dt_to = reference_dt
        dt_from = dt_to - timedelta(hours=24)
        date_str = dt_from.astimezone(tz).date().isoformat()
        date_label = (
            f"{dt_from.astimezone(tz).strftime('%d.%m.%Y %H:%M')} — "
            f"{dt_to.astimezone(tz).strftime('%d.%m.%Y %H:%M')}"
        )
    else:
        today_midnight = reference_dt.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_midnight = today_midnight - timedelta(days=1)
        dt_to = today_midnight
        dt_from = yesterday_midnight
        date_str = yesterday_midnight.date().isoformat()
        date_label = yesterday_midnight.strftime("%d.%m.%Y")

    existing = repo.get_digest(date_str)
    if existing and existing.get("status") == "ok" and existing.get("content"):
        return {"ok": True, "date": date_str, "cached": True, **existing}

    start_datetime = dt_from.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_datetime = dt_to.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    messages = repo.get_digest_messages(
        target_date=None,
        min_score=cfg["min_score"],
        excluded_categories=cfg["excluded_categories"] or None,
        max_per_category=cfg["max_per_category"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )

    if not messages:
        repo.save_digest(date_str, "", 0, "skipped")
        repo.replace_digest_items(date_str, [])
        return {
            "ok": False,
            "error": f"Недостатньо повідомлень (score ≥ {cfg['min_score']}) за {date_label}",
            "date": date_str,
        }

    lines = []
    digest_items: list[dict] = []
    for idx, m in enumerate(messages, start=1):
        cat = m.get("ai_category") or "Інше"
        score = m.get("ai_score") or 0
        source = m.get("source_name") or "?"
        text = (m.get("text") or "").strip()
        lines.append(f"[{cat}, {score}, {source}] {text}")
        digest_items.append(
            {
                "order_index": idx,
                "message_id": int(m.get("id") or 0),
                "source_name": str(source),
                "ai_score": score,
                "ai_category": cat,
                "published_at": m.get("published_at"),
                "text_chars": len(text),
                "included_chars": len(text),
            }
        )
    messages_text = "\n\n".join(lines)
    model_name = getattr(provider, "model", cfg["ai_provider"])

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            provider.generate_digest,
            messages_text,
            cfg["ai_prompt"],
            "article",
            date_label,
        )
        repo.save_digest(date_str, result.content, len(messages), "ok", model_name, result.tokens_in, result.tokens_out)
        repo.replace_digest_items(date_str, digest_items)
        repo.cleanup_old_digests(cfg["keep_days"])
        broadcast_sse("digest_ready", {"date": date_str})
        return {
            "ok": True,
            "date": date_str,
            "message_count": len(messages),
            "content": result.content,
        }
    except Exception as exc:
        err = str(exc)
        repo.save_digest(date_str, "", 0, f"error: {err}")
        repo.replace_digest_items(date_str, [])
        return {"ok": False, "error": err, "date": date_str}


async def _digest_loop() -> None:
    while True:
        try:
            cfg = _get_digest_config()
            if not cfg["enabled"]:
                await asyncio.sleep(300)
                continue

            tz = _KYIV_TZ
            now_local = datetime.now(timezone.utc).astimezone(tz)
            target_hour: int = cfg["hour"]
            target_minute: int = cfg["minute"]
            target_total = target_hour * 60 + target_minute

            next_run = now_local.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            if next_run <= now_local:
                next_run += timedelta(days=1)

            sleep_secs = min((next_run - now_local).total_seconds(), 300)
            await asyncio.sleep(sleep_secs)

            cfg = _get_digest_config()
            if not cfg["enabled"]:
                continue

            now_local = datetime.now(timezone.utc).astimezone(tz)
            diff_min = abs(now_local.hour * 60 + now_local.minute - target_total)
            if diff_min > 6:
                continue

            target_run_dt = now_local.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            await _generate_daily_digest(reference_dt=target_run_dt)

        except Exception:
            await asyncio.sleep(60)
