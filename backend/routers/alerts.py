from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from config import repo
from models import AlertCreate, AlertUpdate
from security import require_admin

router = APIRouter()


@router.get("/api/alerts", dependencies=[Depends(require_admin)])
def list_alerts() -> list[dict]:
    return repo.list_alerts()


@router.post(
    "/api/alerts", status_code=201, dependencies=[Depends(require_admin)]
)
def create_alert(payload: AlertCreate) -> dict:
    pattern = (payload.pattern or "").strip()
    if payload.alert_type == "keyword_ai" and not pattern:
        raise HTTPException(
            status_code=400,
            detail="Для keyword_ai вкажіть ключове слово у pattern",
        )
    if payload.alert_type == "min_score" and payload.min_score is None:
        raise HTTPException(
            status_code=400, detail="Для min_score вкажіть min_score"
        )
    return repo.create_alert(
        name=payload.name.strip(),
        pattern=pattern,
        alert_type=payload.alert_type,
        source_id=payload.source_id,
        min_score=payload.min_score,
        target_chat_id=payload.target_chat_id.strip(),
        is_ai_keyword=payload.is_ai_keyword,
        is_enabled=payload.is_enabled,
    )


@router.patch("/api/alerts/{alert_id}", dependencies=[Depends(require_admin)])
def update_alert(alert_id: int, payload: AlertUpdate) -> dict:
    data = payload.model_dump(exclude_unset=True)
    updated = repo.update_alert(
        alert_id=alert_id,
        name=(data.get("name") or "").strip() if "name" in data else None,
        pattern=(data.get("pattern") or "").strip() if "pattern" in data else None,
        alert_type=data.get("alert_type"),
        source_id=data.get("source_id") if "source_id" in data else None,
        min_score=data.get("min_score") if "min_score" in data else None,
        target_chat_id=(data.get("target_chat_id") or "").strip()
        if "target_chat_id" in data
        else None,
        is_ai_keyword=data.get("is_ai_keyword"),
        is_enabled=data.get("is_enabled"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Alert not found")
    return updated


@router.delete(
    "/api/alerts/{alert_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def delete_alert(alert_id: int) -> None:
    if not repo.delete_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
