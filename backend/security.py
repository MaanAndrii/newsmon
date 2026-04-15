from __future__ import annotations

import hmac
import os
import re
import time
from collections import deque

from fastapi import Header, HTTPException, Request

from config import (
    ADMIN_TOKEN_ENV,
    TELETHON_AUTH_RATE_MAX,
    TELETHON_AUTH_RATE_WINDOW_SECONDS,
    _rate_limit_buckets,
)


def _get_admin_token() -> str:
    return (os.environ.get(ADMIN_TOKEN_ENV) or "").strip()


def require_admin(authorization: str | None = Header(None)) -> None:
    expected = _get_admin_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Сервер не налаштовано: змінна оточення {ADMIN_TOKEN_ENV} не задана. "
                "Адмін має встановити її перед запуском."
            ),
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Потрібен Bearer-токен")
    token = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Невірний токен")


def _client_ip(request_obj: Request) -> str:
    cf = request_obj.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    xff = request_obj.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request_obj.client.host if request_obj.client else "unknown"


def _rate_limit_hit(bucket_key: str, max_hits: int, window_seconds: float) -> bool:
    now_mono = time.monotonic()
    bucket = _rate_limit_buckets.setdefault(bucket_key, deque())
    while bucket and now_mono - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= max_hits:
        return False
    bucket.append(now_mono)
    return True


def _enforce_telethon_auth_rate_limit(request_obj: Request, phone: str) -> None:
    ip = _client_ip(request_obj)
    if not _rate_limit_hit(
        f"tg_auth_ip:{ip}", TELETHON_AUTH_RATE_MAX, TELETHON_AUTH_RATE_WINDOW_SECONDS
    ):
        raise HTTPException(
            status_code=429,
            detail="Забагато спроб з цього IP. Спробуй за 5 хв.",
        )
    phone_key = re.sub(r"[^\d+]", "", phone or "")
    if phone_key and not _rate_limit_hit(
        f"tg_auth_phone:{phone_key}",
        TELETHON_AUTH_RATE_MAX,
        TELETHON_AUTH_RATE_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Забагато спроб для цього номера. Спробуй за 5 хв.",
        )


def _mask_secret(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    value = value.strip()
    if len(value) <= keep:
        return "***"
    return f"***{value[-keep:]}"
