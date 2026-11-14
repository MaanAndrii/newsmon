from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import BinaryIO

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from config import repo
from db import DB_PATH, get_connection, init_db
from security import _get_admin_password, require_admin
from services.lemmatizer import keyword_to_lemma_json

router = APIRouter()

_EXPORT_VERSION = 1
_REQUIRED_TABLES = {"messages", "sources", "categories", "integrations", "settings", "alerts"}
_restore_lock = Lock()


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
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="База даних не знайдена")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        snapshot = Path(file.name)
    try:
        with closing(get_connection()) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
    except Exception:
        snapshot.unlink(missing_ok=True)
        raise

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"newsmon-backup-{ts}.db"
    return FileResponse(
        path=snapshot,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(snapshot.unlink, missing_ok=True),
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

_MAX_UPLOAD = 500 * 1024 * 1024  # 500 MB — covers even very large DB backups


@router.post("/api/import/settings", dependencies=[Depends(require_admin)])
async def import_settings(request: Request) -> dict:
    """Merge configuration from a previously exported JSON file.

    Integrations and settings are overwritten; categories, sources, keywords
    and alerts are upserted (existing records are kept, only new ones added).
    """
    form = await request.form(max_files=1, max_fields=10, max_part_size=_MAX_UPLOAD)
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="Файл не знайдено у запиті")
    raw = await file.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Невірний JSON: {exc}")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Файл не є об'єктом JSON")

    # Accept any version — older exports may have version=1; just import what we can
    if "version" not in data:
        raise HTTPException(status_code=400, detail="Файл не схожий на експорт налаштувань newsmon (відсутнє поле version)")

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
async def import_backup(request: Request) -> dict:
    async with request.form(max_files=1, max_fields=10, max_part_size=_MAX_UPLOAD) as form:
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(400, "Файл не знайдено у запиті")
        if file.size is not None and file.size > _MAX_UPLOAD:
            raise HTTPException(413, "Резервна копія перевищує 500 МБ")
        await run_in_threadpool(_restore_db, file.file)

    return {
        "status": "ok",
        "message": "Базу даних замінено. Попередня версія збережена як newsmon.db.bak. Рекомендується перезапустити сервер.",
    }

def _restore_db(upload: BinaryIO) -> None:
    if upload.read(16) != b"SQLite format 3\x00":
        raise HTTPException(400, "Файл не є SQLite базою даних")
    upload.seek(0)

    with tempfile.TemporaryDirectory(dir=DB_PATH.parent) as directory:
        uploaded = Path(directory) / "upload.db"
        with uploaded.open("wb") as file:
            shutil.copyfileobj(upload, file)

        try:
            with closing(sqlite3.connect(uploaded)) as source:
                integrity = source.execute("PRAGMA quick_check").fetchone()
                if integrity != ("ok",):
                    raise HTTPException(400, "Резервна копія пошкоджена")
                tables = {
                    row[0] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if missing := _REQUIRED_TABLES - tables:
                    raise HTTPException(400, f"Відсутні таблиці NewsMon: {', '.join(sorted(missing))}")

            init_db(uploaded)
            with closing(sqlite3.connect(uploaded)) as source, source:
                source.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("app.admin_password", _get_admin_password()),
                )
        except sqlite3.DatabaseError as exc:
            raise HTTPException(400, "Не вдалося прочитати або оновити схему резервної копії") from exc

        with _restore_lock:
            pending_backup = Path(directory) / "previous.db"
            with closing(get_connection()) as current, closing(sqlite3.connect(pending_backup)) as previous:
                current.backup(previous)
                previous.execute("PRAGMA journal_mode=DELETE")
            pending_backup.replace(DB_PATH.with_suffix(".db.bak"))
            with closing(sqlite3.connect(uploaded)) as source, closing(get_connection()) as current:
                source.backup(current)

# ---------------------------------------------------------------------------
# Timezone setting
# ---------------------------------------------------------------------------

_KNOWN_TIMEZONES = [
    "Europe/Kyiv", "Europe/Warsaw", "Europe/Berlin", "Europe/Paris",
    "Europe/London", "Europe/Moscow", "Europe/Istanbul",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Asia/Almaty", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai", "Asia/Tokyo",
    "Australia/Sydney", "UTC",
]


@router.get("/api/settings/timezone", dependencies=[Depends(require_admin)])
def get_timezone() -> dict:
    tz = repo.get_setting("app.timezone") or "Europe/Kyiv"
    return {"timezone": tz, "known_timezones": _KNOWN_TIMEZONES}


@router.post("/api/settings/timezone", dependencies=[Depends(require_admin)])
def save_timezone(body: dict = Body(...)) -> dict:
    tz = (body.get("timezone") or "").strip()
    if not tz:
        raise HTTPException(status_code=400, detail="Порожній timezone")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)  # validate
    except Exception:
        raise HTTPException(status_code=400, detail=f"Невідомий timezone: {tz}")
    repo.set_setting("app.timezone", tz)
    return {"ok": True, "timezone": tz}


# ---------------------------------------------------------------------------
# Admin password change
# ---------------------------------------------------------------------------

_PASSWORD_MIN_LEN = 4


@router.post("/api/settings/admin-token", dependencies=[Depends(require_admin)])
def change_admin_password(body: dict = Body(...)) -> dict:
    new_password = (body.get("new_token") or "").strip()
    if len(new_password) < _PASSWORD_MIN_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Пароль занадто короткий (мінімум {_PASSWORD_MIN_LEN} символи)",
        )
    repo.set_setting("app.admin_password", new_password)
    return {"ok": True}
