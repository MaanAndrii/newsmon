from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path

from db import Repository

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = ROOT_DIR / "prototype"

# ---------------------------------------------------------------------------
# Monitor constants
# ---------------------------------------------------------------------------
MONITOR_INTERVAL_SECONDS = 600
MIN_MONITOR_INTERVAL_SECONDS = 300
MAX_MONITOR_INTERVAL_SECONDS = 7200
DEFAULT_MONITOR_DEPTH = 3
MIN_MONITOR_DEPTH = 1
MAX_MONITOR_DEPTH = 10
DEFAULT_RETENTION_MONTHS = 3
MIN_RETENTION_MONTHS = 1
MAX_RETENTION_MONTHS = 6

# ---------------------------------------------------------------------------
# Claude constants
# ---------------------------------------------------------------------------
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------
ADMIN_TOKEN_ENV = "NEWSMON_API_TOKEN"
TELETHON_AUTH_RATE_MAX = 3
TELETHON_AUTH_RATE_WINDOW_SECONDS = 300.0

# ---------------------------------------------------------------------------
# Cache TTLs
# ---------------------------------------------------------------------------
TELETHON_STATUS_CACHE_TTL = 30.0
TELETHON_HEALTH_CACHE_TTL = 30.0

# ---------------------------------------------------------------------------
# Shared repository instance
# ---------------------------------------------------------------------------
repo = Repository()

# ---------------------------------------------------------------------------
# Background task handles
# ---------------------------------------------------------------------------
monitor_task: asyncio.Task | None = None
ai_task: asyncio.Task | None = None
digest_task: asyncio.Task | None = None

# SSE client queues — each connected browser gets one queue
_sse_clients: list[asyncio.Queue] = []


def broadcast_sse(event_type: str, data: dict) -> None:
    import json
    payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
    for q in list(_sse_clients):
        try:
            q.put_nowait(payload)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Shared mutable state
# ---------------------------------------------------------------------------
telethon_auth_state: dict[str, dict[str, str]] = {}

telethon_client_lock = asyncio.Lock()
ai_processing_lock = asyncio.Lock()

claude_call_events: deque[dict[str, int | datetime]] = deque(maxlen=5000)
telegram_call_events: deque[datetime] = deque(maxlen=10000)

# In-memory event log (last 50 entries) and collect-cycle history (last 10)
event_log: deque[dict] = deque(maxlen=50)
monitor_run_history: deque[dict] = deque(maxlen=10)

# ---------------------------------------------------------------------------
# Restore debug state from DB so restarts don't lose history
# ---------------------------------------------------------------------------
try:
    for _row in repo.load_api_calls("telegram", hours=168):  # 7 days
        try:
            telegram_call_events.append(
                datetime.fromisoformat(_row["called_at"].replace(" ", "T")).replace(
                    tzinfo=__import__("datetime").timezone.utc
                )
            )
        except Exception:
            pass
    for _row in repo.load_api_calls("claude", hours=168):
        try:
            _at = datetime.fromisoformat(_row["called_at"].replace(" ", "T")).replace(
                tzinfo=__import__("datetime").timezone.utc
            )
            claude_call_events.append({
                "at": _at,
                "input_tokens": int(_row.get("input_tokens") or 0),
                "output_tokens": int(_row.get("output_tokens") or 0),
            })
        except Exception:
            pass
    for _row in repo.load_event_log(limit=50):
        event_log.append(_row)
    for _row in repo.load_run_history(limit=10):
        monitor_run_history.append(_row)
except Exception:
    pass

# Shared counter: AI items processed since the last collect cycle snapshot.
# Using a dict so it's mutated in-place (importable as a reference).
_ai_counters: dict[str, int] = {"processed_since_last_collect": 0}

_telethon_status_cache: dict[str, float | dict | None] = {"at": 0.0, "value": None}
_telethon_health_cache: dict[str, float | dict | None] = {"at": 0.0, "value": None}

_rate_limit_buckets: dict[str, deque[float]] = {}

monitor_status: dict[str, str | int | None] = {
    "state": "stopped",
    "last_run_at": None,
    "last_success_at": None,
    "last_error": None,
    "updated_sources": 0,
    "total_sources": 0,
    "ingested_messages": 0,
    "interval_seconds": MONITOR_INTERVAL_SECONDS,
}
