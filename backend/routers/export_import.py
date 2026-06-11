from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import repo
from db import DB_PATH, get_connection, init_db
from security import require_admin
from services.lemmatizer import keyword_to_lemma_json

router = APIRouter()

_EXPORT_VERSION = 1
_REQUIRED_TABLES = {"messages", "sources", "categories", "integrations", "settings", "alerts"}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.get("/api/export/settings", dependencies=[Depends(require_admin)])
def export_settings() -> JSONResponse:
    """Export all configuration (integrations, settings, sources, categories,
    keywords, alerts) as a JSON file. Includes API keys and Telethon session."""
    integrations = repo.get_integrations()
    settings = repo.list_settings()
    categories = repo.list_categories()
    sources = repo.list_sources(sort_by="alpha")
    keywords = repo.list_keywords()
    alerts = repo.list_alerts()

    cat_id_to_name = {c["id"]: c["name"] for c in categories}
    source_id_to_url = {s["id"]: s["url"] for s in sources}

    payload = {
        "version": _EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "integrations": {k: v for k, v in integrations.items() if k != "updated_at"},
        "settings": settings,
        "categories": [
            {"name": c["name"], "color": c["color"], "is_default": bool(c["is_default"])}
            for c in categories
        ],
        "sources": [
            {
                "name": s["name"],
                "url": s["url"],
                "is_active": bool(s["is_active"]),
                "ai_enabled": bool(s.get("ai_enabled", 1)),
                "digest_enabled": bool(s.get("digest_enabled", 1)),
            }
            for s in sources
        ],
        "keywords": [
            {
                "phrase": kw["phrase"],
                "min_score": kw["min_score"],
                "is_regex": bool(kw["is_regex"]),
                "is_active": bool(kw["is_active"]),
                "category_name": cat_id_to_name.get(kw["category_id"]),
            }
            for kw in keywords
        ],
        "alerts": [
            {
                "name": a["name"],
                "pattern": a["pattern"],
                "alert_type": a["alert_type"],
                "source_url": source_id_to_url.get(a["source_id"]) if a.get("source_id") else None,
                "min_score": a["min_score"],
                "target_chat_id": a["target_chat_id"],
                "is_ai_keyword": bool(a["is_ai_keyword"]),
                "is_enabled": bool(a["is_enabled"]),
                "keyword_lemmas": a.get("keyword_lemmas"),
            }
            for a in alerts
        ],
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="newsmon-settings-{ts}.json"'},
    )


@router.get("/api/export/backup", dependencies=[Depends(require_admin)])
def export_backup() -> FileResponse:
    """Download a full SQLite backup of the entire database (all messages + config)."""
    try:
        with get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass

    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="База даних не знайдена")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"newsmon-backup-{ts}.db"
    return FileResponse(
        path=str(DB_PATH),
        media_type="application/octet-stream",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/api/import/settings", dependencies=[Depends(require_admin)])
async def import_settings(file: UploadFile = File(...)) -> dict:
    """Merge configuration from a previously exported JSON file.

    Integrations and settings are overwritten; categories, sources, keywords
    and alerts are upserted (existing records are kept, only new ones added).
    """
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Невірний JSON: {exc}")

    if data.get("version") != _EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Непідтримувана версія формату: {data.get('version')}. Очікується: {_EXPORT_VERSION}",
        )

    results: dict[str, str] = {}

    # --- integrations ---
    if "integrations" in data:
        try:
            repo.save_integrations(data["integrations"])
            results["integrations"] = "оновлено"
        except Exception as exc:
            results["integrations"] = f"помилка: {exc}"

    # --- settings (key-value) ---
    if "settings" in data:
        ok = 0
        for key, value in data["settings"].items():
            try:
                repo.set_setting(str(key), str(value))
                ok += 1
            except Exception:
                pass
        results["settings"] = f"{ok} ключів"

    # --- categories ---
    if "categories" in data:
        existing_names = {c["name"] for c in repo.list_categories()}
        added = 0
        for cat in data["categories"]:
            name = (cat.get("name") or "").strip()
            if not name or name in existing_names:
                continue
            try:
                repo.create_category(
                    name=name,
                    color=cat.get("color") or "#64748b",
                    is_default=bool(cat.get("is_default")),
                )
                existing_names.add(name)
                added += 1
            except Exception:
                pass
        results["categories"] = f"+{added} нових"

    # --- sources ---
    if "sources" in data:
        existing_urls = {s["url"] for s in repo.list_sources(sort_by="alpha")}
        added = 0
        for src in data["sources"]:
            url = (src.get("url") or "").strip()
            name = (src.get("name") or "").strip()
            if not url or not name or url in existing_urls:
                continue
            try:
                repo.create_source(name=name, url=url)
                existing_urls.add(url)
                added += 1
            except Exception:
                pass
        results["sources"] = f"+{added} нових"

    # --- keywords ---
    if "keywords" in data:
        existing_phrases = {kw["phrase"] for kw in repo.list_keywords()}
        cat_map = {c["name"]: c["id"] for c in repo.list_categories()}
        added = 0
        for kw in data["keywords"]:
            phrase = (kw.get("phrase") or "").strip()
            if not phrase or phrase in existing_phrases:
                continue
            try:
                repo.create_keyword(
                    phrase=phrase,
                    category_id=cat_map.get(kw.get("category_name") or ""),
                    min_score=int(kw.get("min_score") or 0),
                    is_regex=bool(kw.get("is_regex")),
                )
                existing_phrases.add(phrase)
                added += 1
            except Exception:
                pass
        results["keywords"] = f"+{added} нових"

    # --- alerts ---
    if "alerts" in data:
        existing_alert_keys = {
            (a["name"], a["pattern"], a["alert_type"])
            for a in repo.list_alerts()
        }
        source_map = {s["url"]: s["id"] for s in repo.list_sources(sort_by="alpha")}
        added = 0
        for al in data["alerts"]:
            name = (al.get("name") or "").strip()
            pattern = (al.get("pattern") or "").strip()
            alert_type = (al.get("alert_type") or "new_message").strip()
            if not name or (name, pattern, alert_type) in existing_alert_keys:
                continue
            try:
                lemmas = al.get("keyword_lemmas")
                if alert_type == "keyword_ai" and pattern and not lemmas:
                    lemmas = keyword_to_lemma_json(pattern)
                repo.create_alert(
                    name=name,
                    pattern=pattern,
                    alert_type=alert_type,
                    source_id=source_map.get(al.get("source_url") or ""),
                    min_score=al.get("min_score"),
                    target_chat_id=(al.get("target_chat_id") or "").strip(),
                    is_ai_keyword=bool(al.get("is_ai_keyword")),
                    is_enabled=bool(al.get("is_enabled", True)),
                    keyword_lemmas=lemmas,
                )
                existing_alert_keys.add((name, pattern, alert_type))
                added += 1
            except Exception:
                pass
        results["alerts"] = f"+{added} нових"

    return {"status": "ok", "results": results}


@router.post("/api/import/backup", dependencies=[Depends(require_admin)])
async def import_backup(file: UploadFile = File(...)) -> dict:
    """Replace the entire database with an uploaded .db backup file.

    The current database is preserved as newsmon.db.bak before replacement.
    A server restart is recommended after this operation.
    """
    raw = await file.read()

    if not raw[:16].startswith(b"SQLite format 3"):
        raise HTTPException(status_code=400, detail="Файл не є SQLite базою даних")

    # Write to temp file and validate schema
    tmp_path = Path(tempfile.mktemp(suffix=".db"))
    try:
        tmp_path.write_bytes(raw)
        tmp_conn = sqlite3.connect(str(tmp_path))
        found_tables = {
            r[0]
            for r in tmp_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        tmp_conn.close()
        missing = _REQUIRED_TABLES - found_tables
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Файл не схожий на резервну копію newsmon. Відсутні таблиці: {', '.join(sorted(missing))}",
            )
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Помилка перевірки файлу: {exc}")

    # Checkpoint WAL on current DB before replacement
    try:
        with get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass

    # Backup current DB and swap in new one
    bak_path = DB_PATH.with_suffix(".db.bak")
    try:
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, bak_path)
        shutil.move(str(tmp_path), str(DB_PATH))
        init_db()  # apply any missing migrations
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Помилка заміни бази даних: {exc}")

    return {
        "status": "ok",
        "message": "Базу даних замінено. Попередня версія збережена як newsmon.db.bak. Рекомендується перезапустити сервер.",
    }
