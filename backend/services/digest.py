from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from config import broadcast_sse, repo
from services.claude import _record_claude_call, _resolve_claude_model


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
        "timezone": g("timezone", "Europe/Kyiv"),
        "min_score": int(g("min_score", "6")),
        "max_per_category": int(g("max_per_category", "5")),
        "excluded_categories": excluded,
        "format": g("format", "article"),
        "ai_prompt": g("ai_prompt", ""),
        "keep_days": int(g("keep_days", "30")),
    }


def _call_claude_digest_sync(
    api_key: str,
    model: str,
    messages_text: str,
    custom_prompt: str,
    format_style: str,
    date_label: str,
) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Пакет anthropic не встановлено") from exc

    fmt_map = {
        "article": "у форматі журналістської статті з підзаголовками по темах (300–600 слів)",
        "bullets": "у форматі маркованого списку, згрупованого по категоріях",
        "summary": "у форматі короткого executive summary до 200 слів",
    }
    fmt_instruction = fmt_map.get(format_style, fmt_map["article"])

    system_prompt = custom_prompt.strip() or (
        f"Ти редактор новинного видання. На основі повідомлень з моніторингу "
        f"напиши огляд подій за {date_label} {fmt_instruction} українською мовою. "
        f"Виділи найважливіше, вкажи конкретні факти і цифри. "
        f"Не вигадуй деталей, яких немає у вхідних даних."
    )

    client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": messages_text}],
    )
    _record_claude_call(
        int(getattr(response.usage, "input_tokens", 0)),
        int(getattr(response.usage, "output_tokens", 0)),
    )
    return "".join(b.text for b in response.content if hasattr(b, "text")).strip()


async def _generate_daily_digest(target_date: date | str | None = None) -> dict:
    cfg = _get_digest_config()
    integrations = repo.get_integrations()
    api_key = (integrations.get("claude_api_key") or "").strip()
    if not api_key:
        return {"ok": False, "error": "Claude API key не налаштовано"}

    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
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

    model = _resolve_claude_model(integrations.get("claude_model"))
    date_label = target_date.strftime("%d.%m.%Y")

    try:
        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(
            None,
            _call_claude_digest_sync,
            api_key,
            model,
            messages_text,
            cfg["ai_prompt"],
            cfg["format"],
            date_label,
        )
        repo.save_digest(date_str, content, len(messages), "ok")
        repo.cleanup_old_digests(cfg["keep_days"])
        broadcast_sse("digest_ready", {"date": date_str})
        return {
            "ok": True,
            "date": date_str,
            "message_count": len(messages),
            "content": content,
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

            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(cfg["timezone"])
            except Exception:
                tz = timezone.utc

            now_local = datetime.now(timezone.utc).astimezone(tz)
            target_hour: int = cfg["hour"]

            next_run = now_local.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            if next_run <= now_local:
                next_run += timedelta(days=1)

            sleep_secs = min((next_run - now_local).total_seconds(), 300)
            await asyncio.sleep(sleep_secs)

            cfg = _get_digest_config()
            if not cfg["enabled"]:
                continue

            now_local = datetime.now(timezone.utc).astimezone(tz)
            diff_min = abs(now_local.hour * 60 + now_local.minute - target_hour * 60)
            if diff_min > 6:
                continue

            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
            existing = repo.get_digest(yesterday.isoformat())
            if not existing or existing.get("status") not in ("ok",):
                await _generate_daily_digest(yesterday)

        except Exception:
            await asyncio.sleep(60)
