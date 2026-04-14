from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from config import repo
from models import KeywordCreate
from security import require_admin

router = APIRouter()


@router.get("/api/keywords")
def list_keywords() -> list[dict]:
    return repo.list_keywords()


@router.post(
    "/api/keywords", status_code=201, dependencies=[Depends(require_admin)]
)
def create_keyword(payload: KeywordCreate) -> dict:
    try:
        return repo.create_keyword(
            phrase=payload.phrase.strip(),
            category_id=payload.category_id,
            min_score=payload.min_score,
            is_regex=payload.is_regex,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="Keyword already exists for this category"
        ) from exc


@router.delete(
    "/api/keywords/{keyword_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def delete_keyword(keyword_id: int) -> None:
    deleted = repo.delete_keyword(keyword_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Keyword not found")
