from __future__ import annotations

from fastapi import APIRouter

from config import repo

router = APIRouter()


@router.get("/api/stats")
def get_stats(days: int = 30) -> dict:
    safe_days = max(1, min(int(days), 365))
    return {
        "overview": repo.get_stats_overview(),
        "messages_over_time": repo.get_stats_messages_over_time(safe_days),
        "score_distribution": repo.get_stats_score_distribution(safe_days),
        "categories": repo.get_stats_categories(safe_days),
        "top_sources": repo.get_stats_sources(10, safe_days),
        "hourly": repo.get_stats_hours(safe_days),
        "weekday": repo.get_stats_weekday(safe_days),
        "alerts": repo.get_stats_alerts(),
    }
