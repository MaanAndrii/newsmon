from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from config import repo
from models import CategoryCreate
from security import require_admin

router = APIRouter()


@router.get("/api/categories")
def list_categories() -> list[dict]:
    return repo.list_categories()


@router.post(
    "/api/categories", status_code=201, dependencies=[Depends(require_admin)]
)
def create_category(payload: CategoryCreate) -> dict:
    try:
        return repo.create_category(
            name=payload.name.strip(),
            color=payload.color.strip(),
            is_default=payload.is_default,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="Category name already exists"
        ) from exc


@router.delete(
    "/api/categories/{category_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def delete_category(category_id: int) -> None:
    deleted = repo.delete_category(category_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found")
