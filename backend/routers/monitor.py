from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from config import claude_call_events, event_log, monitor_run_history, monitor_status, repo, telegram_call_events
from models import MonitorConfigPayload
from security import require_admin
from services.monitor import _get_monitor_config, _process_ai_queue, _sync_sources_last_messages

router = APIRouter()


@router.get("/api/monitor/status")
def get_monitor_status() -> dict:
    return {**monitor_status, **_get_monitor_config()}


@router.get("/api/monitor/config", dependencies=[Depends(require_admin)])
def get_monitor_config() -> dict:
    return _get_monitor_config()


@router.post("/api/monitor/config", dependencies=[Depends(require_admin)])
def save_monitor_config(payload: MonitorConfigPayload) -> dict:
    repo.set_setting("monitor.collect_enabled", "1" if payload.collect_enabled else "0")
    repo.set_setting("monitor.ai_enabled", "1" if payload.ai_enabled else "0")
    repo.set_setting("monitor.interval_seconds", str(payload.interval_seconds))
    repo.set_setting("monitor.fetch_depth", str(payload.fetch_depth))
    repo.set_setting("monitor.max_messages", str(payload.max_messages))
    repo.set_setting("monitor.ai_prompt", (payload.ai_prompt or "").strip())
    return _get_monitor_config()


@router.post("/api/monitor/run-once", dependencies=[Depends(require_admin)])
async def run_monitor_once() -> dict:
    updated, total, ingested, err = await _sync_sources_last_messages()
    monitor_status["last_run_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    monitor_status["updated_sources"] = updated
    monitor_status["total_sources"] = total
    monitor_status["ingested_messages"] = ingested
    if err:
        monitor_status["state"] = "warning"
        monitor_status["last_error"] = err
    else:
        await _process_ai_queue()
        monitor_status["state"] = "ok"
        monitor_status["last_error"] = None
        monitor_status["last_success_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    return {
        "ok": err is None,
        "updated_sources": updated,
        "total_sources": total,
        "ingested_messages": ingested,
        "detail": err,
    }


@router.get("/api/debug/stats", dependencies=[Depends(require_admin)])
def get_debug_stats() -> dict:
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    hour_ago = now - timedelta(hours=1)
    claude_24h = [
        e
        for e in claude_call_events
        if isinstance(e.get("at"), datetime) and e["at"] >= day_ago
    ]
    telegram_24h = [t for t in telegram_call_events if t >= day_ago]
    telegram_60m = [t for t in telegram_call_events if t >= hour_ago]
    ai_queue = repo.get_ai_queue_stats()
    return {
        "claude_requests_24h": len(claude_24h),
        "claude_input_tokens_24h": sum(
            int(e.get("input_tokens") or 0) for e in claude_24h
        ),
        "claude_output_tokens_24h": sum(
            int(e.get("output_tokens") or 0) for e in claude_24h
        ),
        "telegram_requests_24h": len(telegram_24h),
        "telegram_requests_60m": len(telegram_60m),
        "total_messages": repo.count_messages(),
        "ai_queue_pending": ai_queue.get("pending", 0),
        "ai_queue_processing": ai_queue.get("processing", 0),
        "ai_queue_done": ai_queue.get("done", 0),
        "ai_queue_error": ai_queue.get("error", 0),
    }


@router.get("/api/debug/run-history", dependencies=[Depends(require_admin)])
def get_run_history() -> dict:
    """Return the last 10 collect-cycle snapshots, newest first."""
    return {"runs": list(reversed(list(monitor_run_history)))}


@router.get("/api/debug/log", dependencies=[Depends(require_admin)])
def get_debug_log() -> dict:
    """Return the last 50 event-log entries, newest first."""
    return {"events": list(reversed(list(event_log)))}
