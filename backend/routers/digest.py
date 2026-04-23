from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from config import repo
from models import DigestConfigPayload
from security import require_admin
from services.digest import _generate_daily_digest, _get_digest_config

router = APIRouter()


@router.get("/api/digest/config", dependencies=[Depends(require_admin)])
def get_digest_config() -> dict:
    return _get_digest_config()


@router.post("/api/digest/config", dependencies=[Depends(require_admin)])
def save_digest_config(payload: DigestConfigPayload) -> dict:
    repo.set_setting("digest.enabled", "1" if payload.enabled else "0")
    repo.set_setting("digest.hour", str(payload.hour))
    repo.set_setting("digest.minute", str(payload.minute))
    repo.set_setting("digest.min_score", str(payload.min_score))
    repo.set_setting("digest.max_per_category", str(payload.max_per_category))
    repo.set_setting("digest.excluded_categories", json.dumps(payload.excluded_categories))
    repo.set_setting("digest.ai_prompt", (payload.ai_prompt or "").strip())
    repo.set_setting("digest.keep_days", str(payload.keep_days))
    repo.set_setting("digest.ai_provider", payload.ai_provider)
    repo.set_setting("digest.ai_model", (payload.ai_model or "").strip())
    repo.set_setting("digest.mode", payload.mode)
    return _get_digest_config()


@router.get("/api/digest/list")
def list_digests(limit: int = 7) -> dict:
    return {"digests": repo.list_digests(limit=min(limit, 30))}


@router.post("/api/digest/generate", dependencies=[Depends(require_admin)])
async def generate_digest() -> dict:
    result = await _generate_daily_digest()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Помилка генерації"))
    return result


@router.delete("/api/digest/{digest_date}", dependencies=[Depends(require_admin)])
def delete_digest(digest_date: str) -> dict:
    deleted = repo.delete_digest(digest_date)
    if not deleted:
        raise HTTPException(status_code=404, detail="Дайджест не знайдено")
    return {"ok": True}


@router.get("/api/digest/{digest_date}")
def get_digest(digest_date: str) -> dict:
    digest = repo.get_digest(digest_date)
    if not digest:
        raise HTTPException(status_code=404, detail="Дайджест не знайдено")
    return digest


@router.get("/api/digest/{digest_date}/stats", dependencies=[Depends(require_admin)])
def get_digest_stats(digest_date: str) -> dict:
    stats = repo.get_digest_stats(digest_date)
    if not stats:
        raise HTTPException(status_code=404, detail="Дайджест не знайдено")
    return stats
