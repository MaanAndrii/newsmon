from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from config import (
    ROOT_DIR,
    _telethon_health_cache,
    _telethon_status_cache,
    monitor_status,
    repo,
)

def _telethon_session_base() -> Path:
    return ROOT_DIR / "backend" / "telegram_user"


def _telethon_session_file() -> Path:
    return _telethon_session_base().with_suffix(".session")


def _quarantine_telethon_session(reason: str) -> None:
    base = _telethon_session_base()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for suffix in [".session", ".session-journal", ".session-wal", ".session-shm"]:
        src = Path(f"{base}{suffix}")
        if src.exists():
            dst = src.with_name(f"{src.name}.broken_{stamp}")
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                continue
    monitor_status["last_error"] = f"Telethon session reset: {reason}"


def _is_telethon_auth_error(exc: Exception) -> bool:
    """Return True for auth/session-invalid errors that require re-login."""
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    name_markers = (
        "authkeyunregistered",
        "sessionrevoked",
        "authkeyduplicated",
        "unauthorizederror",
    )
    text_markers = (
        "auth key is not registered",
        "key is not registered in the system",
        "session revoked",
        "telethon-сесія не авторизована",
        "session is not authorized",
    )
    if any(marker in name for marker in name_markers):
        return True
    return any(marker in text for marker in text_markers)


def _reset_telethon_session_for_reauth(reason: str) -> None:
    """Clear both file and string sessions so a clean login can be performed."""
    try:
        repo.set_setting("telethon.string_session", "")
    except Exception:
        pass
    _quarantine_telethon_session(reason)
    _invalidate_telethon_status_caches()


def _get_saved_string_session() -> str | None:
    value = repo.get_setting("telethon.string_session", None)
    if not value:
        return None
    value = value.strip()
    return value or None


def _telethon_client_init_data(api_id: int, api_hash: str) -> tuple[str, int, str]:
    string_session = _get_saved_string_session()
    if string_session:
        return ("string", api_id, api_hash)
    return ("file", api_id, api_hash)


def _telethon_client_config() -> tuple[int, str]:
    import re
    integrations = repo.get_integrations()
    api_id = (integrations.get("telegram_api_id") or "").strip()
    api_hash = (integrations.get("telegram_api_hash") or "").strip()
    if not re.fullmatch(r"\d{5,12}", api_id) or not re.fullmatch(
        r"[a-fA-F0-9]{32}", api_hash
    ):
        raise HTTPException(
            status_code=400,
            detail="Вкажіть коректні Telegram API ID/Hash у вкладці інтеграцій",
        )
    return int(api_id), api_hash


def _telethon_client_kwargs() -> dict[str, str]:
    # Keep Telethon defaults instead of spoofing a mobile device/app signature.
    # This avoids suspicious client fingerprinting and keeps session behavior
    # consistent with the runtime environment.
    return {}


def _chmod_session_files(quiet: bool = True) -> None:
    base = _telethon_session_base()
    for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
        path = Path(f"{base}{suffix}")
        if not path.exists():
            continue
        try:
            os.chmod(path, 0o600)
        except OSError:
            if not quiet:
                raise


def _invalidate_telethon_status_caches() -> None:
    _telethon_status_cache["at"] = 0.0
    _telethon_status_cache["value"] = None
    _telethon_health_cache["at"] = 0.0
    _telethon_health_cache["value"] = None
