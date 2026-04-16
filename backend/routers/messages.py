from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from config import repo
from models import ClearMessagesPayload
from security import require_admin

router = APIRouter()


@router.get("/api/messages")
def list_messages(
    limit: int = 100,
    q: str | None = None,
    category: str | None = None,
    source_id: int | None = None,
    keyword: str | None = None,
    min_score: int | None = None,
) -> list[dict]:
    safe_limit = max(1, min(limit, 500))
    search_query = (q or "").strip() or None
    category_filter = (category or "").strip() or None
    keyword_filter = (keyword or "").strip() or None
    source_filter = source_id if source_id and source_id > 0 else None
    score_filter = min_score if min_score is not None and 1 <= min_score <= 10 else None
    return repo.list_messages(
        limit=safe_limit,
        search_query=search_query,
        category=category_filter,
        source_id=source_filter,
        keyword=keyword_filter,
        min_score=score_filter,
    )


@router.get("/api/filters/keywords")
def list_filter_keywords() -> list[str]:
    return repo.list_alert_keywords()


@router.post(
    "/api/messages/clear-all", dependencies=[Depends(require_admin)]
)
def clear_all_messages(payload: ClearMessagesPayload) -> dict:
    if not payload.confirm:
        raise HTTPException(
            status_code=400, detail="Підтвердіть очищення (confirm=true)"
        )
    deleted = repo.clear_all_messages()
    return {"ok": True, "deleted_messages": deleted}


@router.post(
    "/api/messages/clear-empty", dependencies=[Depends(require_admin)]
)
def clear_empty_messages() -> dict:
    deleted = repo.delete_empty_messages()
    return {"ok": True, "deleted_messages": deleted}
