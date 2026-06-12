from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from config import ai_raw_logs, claude_call_events, event_log, monitor_run_history, monitor_status, repo, telegram_call_events
from models import DashboardHeartbeatPayload, MonitorConfigPayload
from security import _rate_limit_hit, require_admin
from services.monitor import _get_monitor_config
from utils import _resolve_client_ip

router = APIRouter()


@router.get("/api/monitor/status")
def get_monitor_status() -> dict:
    public = {k: v for k, v in monitor_status.items() if k != "last_error"}
    cfg = _get_monitor_config()
    try:
        ai_stats = repo.get_ai_queue_stats()
    except Exception:
        ai_stats = {}
    return {
        **public,
        **cfg,
        "ai_queue_pending": ai_stats.get("pending", 0),
        "ai_queue_processing": ai_stats.get("processing", 0),
    }


@router.get("/api/monitor/config", dependencies=[Depends(require_admin)])
def get_monitor_config() -> dict:
    return _get_monitor_config()


@router.post("/api/monitor/config", dependencies=[Depends(require_admin)])
def save_monitor_config(payload: MonitorConfigPayload) -> dict:
    repo.set_setting("monitor.collect_enabled", "1" if payload.collect_enabled else "0")
    repo.set_setting("monitor.ai_enabled", "1" if payload.ai_enabled else "0")
    repo.set_setting("monitor.dedup_enabled", "1" if payload.dedup_enabled else "0")
    repo.set_setting("monitor.retention_months", str(payload.retention_months))
    repo.set_setting("monitor.ai_prompt", (payload.ai_prompt or "").strip())
    repo.set_setting("monitor.ai_provider", payload.ai_provider)
    repo.set_setting("monitor.ai_model", (payload.ai_model or "").strip())
    return _get_monitor_config()


@router.get("/api/debug/stats", dependencies=[Depends(require_admin)])
def get_debug_stats() -> dict:
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    two_days_ago = now - timedelta(hours=48)
    hour_ago = now - timedelta(hours=1)
    all_ai_24h = [
        e
        for e in claude_call_events
        if isinstance(e.get("at"), datetime) and e["at"] >= day_ago
    ]
    claude_24h = [e for e in all_ai_24h if e.get("provider") == "claude"]
    grok_24h = [e for e in all_ai_24h if e.get("provider") == "grok"]
    gemini_24h = [e for e in all_ai_24h if e.get("provider") == "gemini"]
    deepseek_24h = [e for e in all_ai_24h if e.get("provider") == "deepseek"]
    telegram_24h = [t for t in telegram_call_events if t >= day_ago]
    telegram_48h = [t for t in telegram_call_events if t >= two_days_ago]
    telegram_60m = [t for t in telegram_call_events if t >= hour_ago]
    ai_queue = repo.get_ai_queue_stats()
    dedup = repo.get_dedup_stats()
    return {
        "claude_requests_24h": len(claude_24h),
        "claude_input_tokens_24h": sum(
            int(e.get("input_tokens") or 0) for e in claude_24h
        ),
        "claude_output_tokens_24h": sum(
            int(e.get("output_tokens") or 0) for e in claude_24h
        ),
        "grok_requests_24h": len(grok_24h),
        "grok_input_tokens_24h": sum(int(e.get("input_tokens") or 0) for e in grok_24h),
        "grok_output_tokens_24h": sum(int(e.get("output_tokens") or 0) for e in grok_24h),
        "gemini_requests_24h": len(gemini_24h),
        "gemini_input_tokens_24h": sum(int(e.get("input_tokens") or 0) for e in gemini_24h),
        "gemini_output_tokens_24h": sum(int(e.get("output_tokens") or 0) for e in gemini_24h),
        "deepseek_requests_24h": len(deepseek_24h),
        "deepseek_input_tokens_24h": sum(int(e.get("input_tokens") or 0) for e in deepseek_24h),
        "deepseek_output_tokens_24h": sum(int(e.get("output_tokens") or 0) for e in deepseek_24h),
        "telegram_requests_24h": len(telegram_24h),
        "telegram_requests_48h": len(telegram_48h),
        "telegram_requests_60m": len(telegram_60m),
        "total_messages": repo.count_messages(),
        "ai_queue_pending": ai_queue.get("pending", 0),
        "ai_queue_processing": ai_queue.get("processing", 0),
        "ai_queue_done": ai_queue.get("done", 0),
        "ai_queue_error": ai_queue.get("error", 0),
        "dedup_total": dedup["dedup_total"],
        "dedup_24h": dedup["dedup_24h"],
    }


@router.get("/api/debug/run-history", dependencies=[Depends(require_admin)])
def get_run_history() -> dict:
    """Return the last 10 collect-cycle snapshots, newest first."""
    return {"runs": list(reversed(list(monitor_run_history)))}


@router.get("/api/debug/log", dependencies=[Depends(require_admin)])
def get_debug_log() -> dict:
    """Return the last 100 event-log entries, newest first."""
    return {"events": list(reversed(list(event_log)))}


@router.get("/api/debug/ai-logs", dependencies=[Depends(require_admin)])
def get_ai_raw_logs() -> dict:
    """Return the last 10 raw AI request/response pairs, newest first."""
    return {"logs": list(reversed(list(ai_raw_logs)))}


@router.post("/api/debug/dashboard-usage/heartbeat")
def dashboard_usage_heartbeat(payload: DashboardHeartbeatPayload, request: Request) -> dict:
    ip = _resolve_client_ip(request)
    if not _rate_limit_hit(f"heartbeat:{ip}", 10, 60.0):
        raise HTTPException(status_code=429, detail="Занадто багато запитів")
    user_agent = (request.headers.get("User-Agent") or "").strip()
    repo.record_dashboard_session(
        session_key=payload.session_key,
        ip=ip,
        active_seconds=payload.active_seconds,
        user_agent=user_agent,
        language=payload.language,
        timezone=payload.timezone,
        screen=payload.screen,
        path=payload.path,
    )
    return {"ok": True}


@router.get("/api/debug/dashboard-users", dependencies=[Depends(require_admin)])
def get_dashboard_users(limit: int = 200) -> dict:
    return {"users": repo.list_dashboard_sessions(limit=limit, since_hours=24)}
