from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from config import broadcast_sse, repo
from services.providers import get_provider

try:
    from zoneinfo import ZoneInfo
    _KYIV_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    _KYIV_TZ = timezone.utc


def _kyiv_yesterday() -> date:
    return (datetime.now(timezone.utc).astimezone(_KYIV_TZ) - timedelta(days=1)).date()


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
        "format": g("format", "article"),
        "ai_prompt": g("ai_prompt", ""),
        "keep_days": int(g("keep_days", "30")),
        "ai_provider": g("ai_provider", "claude"),
    }


async def _generate_daily_digest(target_date: date | str | None = None) -> dict:
    cfg = _get_digest_config()
    integrations = repo.get_integrations()

    provider = get_provider(cfg["ai_provider"], integrations)
    if not provider.has_credentials():
        return {"ok": False, "error": f"API ключ або модель для провайдера '{cfg['ai_provider']}' не налаштовані"}

    if target_date is None:
        target_date = _kyiv_yesterday()
    elif isinstance(target_date, str):
        try:
            target_date = date.fromisoformat(target_date)
        except ValueError:
            return {"ok": False, "error": f"Невірний формат дати: {target_date}"}

    date_str = target_date.isoformat()

    existing = repo.get_digest(date_str)
    if existing and existing.get("status") == "ok" and existing.get("content"):
        return {"ok": True, "date": date_str, "cached": True, **existing}

    messages = repo.get_digest_messages(
        target_date=date_str,
        min_score=cfg["min_score"],
        excluded_categories=cfg["excluded_categories"] or None,
        max_per_category=cfg["max_per_category"],
    )

    if not messages:
        repo.save_digest(date_str, "", 0, "skipped")
        return {
            "ok": False,
            "error": f"Недостатньо повідомлень (score ≥ {cfg['min_score']}) за {date_str}",
            "date": date_str,
        }

    lines = []
    for m in messages:
        cat = m.get("ai_category") or "Інше"
        score = m.get("ai_score") or 0
        source = m.get("source_name") or "?"
        text = (m.get("text") or "").strip()[:200]
        lines.append(f"[{cat}, {score}, {source}] {text}")
    messages_text = "\n\n".join(lines)

    date_label = target_date.strftime("%d.%m.%Y")
    model_name = getattr(provider, "model", cfg["ai_provider"])

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            provider.generate_digest,
            messages_text,
            cfg["ai_prompt"],
            cfg["format"],
            date_label,
        )
        repo.save_digest(date_str, result.content, len(messages), "ok", model_name, result.tokens_in, result.tokens_out)
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

            yesterday = _kyiv_yesterday()
            existing = repo.get_digest(yesterday.isoformat())
            if not existing or existing.get("status") not in ("ok",):
                await _generate_daily_digest(yesterday)

        except Exception:
            await asyncio.sleep(60)
