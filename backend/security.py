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
from utils import _resolve_client_ip

_DEFAULT_PASSWORD = "admin"


def _get_admin_password() -> str:
    """Return the active admin password.

    Priority: DB setting → legacy NEWSMON_API_TOKEN env var → "admin" default.
    """
    try:
        from config import repo
        db_pass = (repo.get_setting("app.admin_password") or "").strip()
        if db_pass:
            return db_pass
    except Exception:
        pass
    env_pass = (os.environ.get(ADMIN_TOKEN_ENV) or "").strip()
    if env_pass:
        return env_pass
    return _DEFAULT_PASSWORD


def require_admin(authorization: str | None = Header(None)) -> None:
    expected = _get_admin_password()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Потрібен пароль")
    token = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Невірний пароль")


def _rate_limit_hit(bucket_key: str, max_hits: int, window_seconds: float) -> bool:
    now_mono = time.monotonic()
    bucket = _rate_limit_buckets.setdefault(bucket_key, deque())
    while bucket and now_mono - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= max_hits:
        return False
    bucket.append(now_mono)
    # Prune empty deques to prevent unbounded dict growth
    if len(_rate_limit_buckets) > 10_000:
        stale = [k for k, v in _rate_limit_buckets.items() if not v]
        for k in stale:
            _rate_limit_buckets.pop(k, None)
    return True


def _enforce_telethon_auth_rate_limit(request_obj: Request, phone: str) -> None:
    ip = _resolve_client_ip(request_obj)
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
