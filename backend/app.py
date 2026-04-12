from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db import Repository, init_db

app = FastAPI(title="NewsMon Prototype API", version="0.1.0")
repo = Repository()
ROOT_DIR = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = ROOT_DIR / "prototype"


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=3, max_length=255)


class SourceUpdate(BaseModel):
    is_active: bool | None = None
    ai_enabled: bool | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    color: str = Field(default="#64748b", min_length=4, max_length=20)
    is_default: bool = False


class KeywordCreate(BaseModel):
    phrase: str = Field(min_length=2, max_length=120)
    category_id: int | None = None
    min_score: int = Field(default=0, ge=0, le=10)
    is_regex: bool = False


class IntegrationsPayload(BaseModel):
    claude_api_key: str | None = None
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_bot_token: str | None = None
    telegram_bot_chat_id: str | None = None


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/sources")
def list_sources() -> list[dict]:
    return repo.list_sources()


@app.post("/api/sources", status_code=201)
def create_source(payload: SourceCreate) -> dict:
    try:
        return repo.create_source(payload.name.strip(), payload.url.strip())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Source URL already exists") from exc


@app.patch("/api/sources/{source_id}")
def update_source(source_id: int, payload: SourceUpdate) -> dict:
    updated = repo.update_source(source_id, payload.is_active, payload.ai_enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return updated


@app.delete("/api/sources/{source_id}", status_code=204)
def delete_source(source_id: int) -> None:
    deleted = repo.delete_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found")


@app.get("/api/categories")
def list_categories() -> list[dict]:
    return repo.list_categories()


@app.post("/api/categories", status_code=201)
def create_category(payload: CategoryCreate) -> dict:
    try:
        return repo.create_category(
            name=payload.name.strip(),
            color=payload.color.strip(),
            is_default=payload.is_default,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Category name already exists") from exc


@app.get("/api/keywords")
def list_keywords() -> list[dict]:
    return repo.list_keywords()


@app.post("/api/keywords", status_code=201)
def create_keyword(payload: KeywordCreate) -> dict:
    try:
        return repo.create_keyword(
            phrase=payload.phrase.strip(),
            category_id=payload.category_id,
            min_score=payload.min_score,
            is_regex=payload.is_regex,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Keyword already exists for this category") from exc


@app.get("/api/integrations")
def get_integrations() -> dict:
    return repo.get_integrations()


@app.post("/api/integrations")
def save_integrations(payload: IntegrationsPayload) -> dict:
    data = payload.model_dump()
    clean = {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
    return repo.save_integrations(clean)


@app.post("/api/integrations/validate")
def validate_integrations(payload: IntegrationsPayload) -> dict:
    data = payload.model_dump()
    claude = bool(data.get("claude_api_key")) and data["claude_api_key"].startswith("sk-")
    telegram_user = bool(data.get("telegram_api_id")) and bool(data.get("telegram_api_hash"))
    telegram_bot = bool(data.get("telegram_bot_token")) and bool(data.get("telegram_bot_chat_id"))
    return {
        "claude": {"ok": claude},
        "telegram_user_api": {"ok": telegram_user},
        "telegram_bot_api": {"ok": telegram_bot},
        "overall_ok": claude and telegram_user and telegram_bot,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PROTOTYPE_DIR / "dashboard.html")


@app.get("/dashboard.html")
def dashboard_page() -> FileResponse:
    return FileResponse(PROTOTYPE_DIR / "dashboard.html")


@app.get("/settings.html")
def settings_page() -> FileResponse:
    return FileResponse(PROTOTYPE_DIR / "settings.html")
