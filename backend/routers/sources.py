from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from config import repo
from models import SourceCreate, SourceUpdate
from security import require_admin
from services.telegram import (
    _canonical_source_url,
    _extract_telegram_username,
    _fetch_telegram_channel_title,
)

router = APIRouter()


@router.get("/api/sources")
def list_sources(sort: str = "created_desc") -> list[dict]:
    items = repo.list_sources(sort_by=sort)
    now = datetime.now(timezone.utc)
    for item in items:
        signal = "red"
        last_message_at = item.get("last_message_at")
        if last_message_at:
            try:
                dt = datetime.strptime(last_message_at, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                hours = (now - dt).total_seconds() / 3600
                if hours <= 12:
                    signal = "green"
                elif hours <= 24:
                    signal = "yellow"
                else:
                    signal = "red"
            except ValueError:
                signal = "red"
        item["last_message_signal"] = signal
    return items


@router.post("/api/sources", status_code=201, dependencies=[Depends(require_admin)])
def create_source(payload: SourceCreate) -> dict:
    username = _extract_telegram_username(payload.url)
    if not username:
        raise HTTPException(
            status_code=400,
            detail="Невалідне джерело. Використай @username або https://t.me/username",
        )
    title = _fetch_telegram_channel_title(username)
    if not title:
        raise HTTPException(
            status_code=400,
            detail="Не вдалося перевірити доступність каналу або отримати його назву",
        )
    canonical_url = _canonical_source_url(username)
    existing_same_username = [
        src
        for src in repo.list_sources()
        if (_extract_telegram_username(src.get("url", "")) or "").lower()
        == username.lower()
    ]
    if existing_same_username:
        raise HTTPException(status_code=409, detail="Source для цього каналу вже існує")
    try:
        return repo.create_source(title, canonical_url)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Source URL already exists") from exc


@router.patch("/api/sources/{source_id}", dependencies=[Depends(require_admin)])
def update_source(source_id: int, payload: SourceUpdate) -> dict:
    updated = repo.update_source(source_id, payload.is_active, payload.ai_enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return updated


@router.delete(
    "/api/sources/{source_id}", status_code=204, dependencies=[Depends(require_admin)]
)
def delete_source(source_id: int) -> None:
    deleted = repo.delete_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found")
