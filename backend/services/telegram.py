from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib import parse, request

from config import telegram_call_events


def _record_telegram_call() -> None:
    telegram_call_events.append(datetime.now(timezone.utc))


def _extract_telegram_username(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith("@"):
        value = value[1:]
    if "t.me/" in value:
        path = parse.urlparse(value).path.strip("/")
        value = path.split("/")[0] if path else ""
    if re.fullmatch(r"[A-Za-z0-9_]{5,64}", value):
        return value
    return None


def _canonical_source_url(username: str) -> str:
    return f"https://t.me/{username.lower()}"


def _fetch_telegram_channel_title(username: str) -> str | None:
    url = f"https://t.me/{username}"
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=8) as resp:
            if resp.status != 200:
                return None
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    og_title = re.search(
        r'<meta property="og:title" content="([^"]+)"',
        html,
        flags=re.IGNORECASE,
    )
    if og_title and og_title.group(1).strip():
        return og_title.group(1).strip()

    page_title = re.search(r"<title>([^<]+)</title>", html, flags=re.IGNORECASE)
    if page_title and page_title.group(1).strip():
        return page_title.group(1).replace("Telegram:", "").strip()
    return None


def _send_telegram_bot_message(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=12):
        _record_telegram_call()
