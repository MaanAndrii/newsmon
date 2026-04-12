from __future__ import annotations

import re
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse, request

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db import Repository, init_db

app = FastAPI(title="NewsMon Prototype API", version="0.1.0")
repo = Repository()
ROOT_DIR = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = ROOT_DIR / "prototype"
MONITOR_INTERVAL_SECONDS = 300
monitor_task: asyncio.Task | None = None
telethon_auth_state: dict[str, str] = {}
monitor_status: dict[str, str | int | None] = {
    "state": "stopped",
    "last_run_at": None,
    "last_success_at": None,
    "last_error": None,
    "updated_sources": 0,
    "total_sources": 0,
}


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
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None


class TelethonCodeRequest(BaseModel):
    phone: str


class TelethonCodeVerify(BaseModel):
    phone: str
    code: str
    password: str | None = None


def _extract_telegram_username(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith("@"):
        value = value[1:]
    if "t.me/" in value:
        path = parse.urlparse(value).path.strip("/")
        value = path.split("/")[0] if path else ""
    if re.fullmatch(r"[A-Za-z0-9_]{5,64}", value):
        return value
    return None


def _fetch_telegram_channel_title(username: str) -> str | None:
    url = f"https://t.me/{username}"
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=8) as resp:
            if resp.status != 200:
                return None
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    og_title = re.search(
        r'<meta property="og:title" content="([^"]+)"',
        html,
        flags=re.IGNORECASE,
    )
    if og_title and og_title.group(1).strip():
        return og_title.group(1).strip()

    page_title = re.search(r"<title>([^<]+)</title>", html, flags=re.IGNORECASE)
    if page_title and page_title.group(1).strip():
        return page_title.group(1).replace("Telegram:", "").strip()
    return None


async def _sync_sources_last_messages() -> tuple[int, int, str | None]:
    integrations = repo.get_integrations()
    api_id = (integrations.get("telegram_api_id") or "").strip()
    api_hash = (integrations.get("telegram_api_hash") or "").strip()
    session_name = "telegram_user"

    if not re.fullmatch(r"\d{5,12}", api_id) or not re.fullmatch(
        r"[a-fA-F0-9]{32}", api_hash
    ):
        return 0, 0, "Telegram User API ID/Hash не заповнені або некоректні"

    try:
        from telethon import TelegramClient
    except ImportError:
        return 0, 0, "Telethon не встановлено (виконайте: pip install -r backend/requirements.txt)"

    sources = repo.list_sources(sort_by="alpha")
    session_path = ROOT_DIR / "backend" / session_name
    updated = 0
    async with TelegramClient(str(session_path), int(api_id), api_hash) as client:
        if not await client.is_user_authorized():
            return 0, len(sources), "Telethon-сесія не авторизована (потрібен login)"
        for source in sources:
            username = _extract_telegram_username(source["url"] or "")
            if not username:
                continue
            try:
                entity = await client.get_entity(username)
                messages = await client.get_messages(entity, limit=1)
                if messages and messages[0] and messages[0].date:
                    dt_utc = messages[0].date.astimezone(timezone.utc)
                    repo.update_source_last_message(
                        source["id"],
                        dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    updated += 1
            except Exception:
                continue
    return updated, len(sources), None


def _telethon_client_config() -> tuple[int, str]:
    integrations = repo.get_integrations()
    api_id = (integrations.get("telegram_api_id") or "").strip()
    api_hash = (integrations.get("telegram_api_hash") or "").strip()
    if not re.fullmatch(r"\d{5,12}", api_id) or not re.fullmatch(
        r"[a-fA-F0-9]{32}", api_hash
    ):
        raise HTTPException(
            status_code=400,
            detail="Вкажіть коректні Telegram API ID/Hash у вкладці інтеграцій",
        )
    return int(api_id), api_hash


async def _monitor_loop() -> None:
    while True:
        try:
            monitor_status["state"] = "running"
            monitor_status["last_run_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            updated, total, err = await _sync_sources_last_messages()
            monitor_status["updated_sources"] = updated
            monitor_status["total_sources"] = total
            if err:
                monitor_status["state"] = "warning"
                monitor_status["last_error"] = err
            else:
                monitor_status["state"] = "ok"
                monitor_status["last_error"] = None
                monitor_status["last_success_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
        except Exception:
            monitor_status["state"] = "error"
            monitor_status["last_error"] = "Непередбачена помилка моніторингу"
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    global monitor_task
    if monitor_task is None:
        monitor_task = asyncio.create_task(_monitor_loop())


@app.get("/api/monitor/status")
def get_monitor_status() -> dict:
    return monitor_status


@app.get("/api/telethon/auth/status")
async def telethon_auth_status() -> dict:
    api_id, api_hash = _telethon_client_config()
    session_name = "telegram_user"
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telethon не встановлено. Виконайте: pip install -r backend/requirements.txt",
        ) from exc
    session_path = ROOT_DIR / "backend" / session_name
    async with TelegramClient(str(session_path), api_id, api_hash) as client:
        authorized = await client.is_user_authorized()
    return {"authorized": authorized, "session": session_name}


@app.post("/api/telethon/auth/request-code")
async def telethon_request_code(payload: TelethonCodeRequest) -> dict:
    phone = payload.phone.strip()
    if not re.fullmatch(r"\+?\d{10,15}", phone):
        raise HTTPException(status_code=400, detail="Невірний формат телефону")
    api_id, api_hash = _telethon_client_config()
    session_name = "telegram_user"
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telethon не встановлено. Виконайте: pip install -r backend/requirements.txt",
        ) from exc
    session_path = ROOT_DIR / "backend" / session_name
    async with TelegramClient(str(session_path), api_id, api_hash) as client:
        try:
            sent = await client.send_code_request(phone)
            telethon_auth_state[phone] = sent.phone_code_hash
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Не вдалося надіслати код: {exc}",
            ) from exc
    return {"ok": True, "detail": "Код підтвердження надіслано", "phone": phone}


@app.post("/api/telethon/auth/verify-code")
async def telethon_verify_code(payload: TelethonCodeVerify) -> dict:
    phone = payload.phone.strip()
    code = payload.code.strip()
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Вкажіть телефон і код")
    phone_code_hash = telethon_auth_state.get(phone)
    if not phone_code_hash:
        raise HTTPException(status_code=400, detail="Спочатку запитай код підтвердження")

    api_id, api_hash = _telethon_client_config()
    session_name = "telegram_user"
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            PhoneCodeExpiredError,
            PhoneCodeInvalidError,
            SessionPasswordNeededError,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telethon не встановлено. Виконайте: pip install -r backend/requirements.txt",
        ) from exc
    session_path = ROOT_DIR / "backend" / session_name
    async with TelegramClient(str(session_path), api_id, api_hash) as client:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except PhoneCodeInvalidError as exc:
            raise HTTPException(status_code=400, detail="Невірний код підтвердження") from exc
        except PhoneCodeExpiredError as exc:
            telethon_auth_state.pop(phone, None)
            raise HTTPException(status_code=400, detail="Код прострочений. Запросіть новий код") from exc
        except SessionPasswordNeededError:
            if not payload.password:
                raise HTTPException(
                    status_code=400,
                    detail="Потрібен пароль 2FA (Telegram password)",
                )
            try:
                await client.sign_in(password=payload.password)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Невірний пароль 2FA") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Помилка авторизації: {exc}") from exc
        authorized = await client.is_user_authorized()
    telethon_auth_state.pop(phone, None)
    return {"ok": authorized, "detail": "Telethon авторизовано" if authorized else "Авторизація не завершена"}


@app.get("/api/sources")
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


@app.post("/api/sources", status_code=201)
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

    try:
        canonical_url = f"https://t.me/{username}"
        return repo.create_source(title, canonical_url)
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
    telegram_api_id = (data.get("telegram_api_id") or "").strip()
    telegram_api_hash = (data.get("telegram_api_hash") or "").strip()
    telegram_user_format = bool(
        re.fullmatch(r"\d{5,12}", telegram_api_id)
        and re.fullmatch(r"[a-fA-F0-9]{32}", telegram_api_hash)
    )
    telegram_user_reason = None if telegram_user_format else "API ID має бути числом, API Hash — 32 hex-символи"
    return {
        "telegram_user_api": {
            "ok": telegram_user_format,
            "reason": telegram_user_reason,
        },
        "telethon": {
            "ok": telegram_user_format,
            "reason": None if telegram_user_format else "Для Telethon потрібні коректні Telegram API ID/Hash",
        },
        "overall_ok": telegram_user_format,
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
