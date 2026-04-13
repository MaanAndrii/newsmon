from __future__ import annotations

import re
import sqlite3
import asyncio
import json
import shutil
from datetime import timedelta
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
MONITOR_INTERVAL_SECONDS = 600
MIN_MONITOR_INTERVAL_SECONDS = 300
MAX_MONITOR_INTERVAL_SECONDS = 1800
DEFAULT_MONITOR_DEPTH = 3
MIN_MONITOR_DEPTH = 1
MAX_MONITOR_DEPTH = 10
CLAUDE_MODELS = {
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
}
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"
monitor_task: asyncio.Task | None = None
telethon_auth_state: dict[str, dict[str, str]] = {}
telethon_client_lock = asyncio.Lock()
ai_processing_lock = asyncio.Lock()
monitor_status: dict[str, str | int | None] = {
    "state": "stopped",
    "last_run_at": None,
    "last_success_at": None,
    "last_error": None,
    "updated_sources": 0,
    "total_sources": 0,
    "ingested_messages": 0,
    "interval_seconds": MONITOR_INTERVAL_SECONDS,
}


def _telethon_session_base() -> Path:
    return ROOT_DIR / "backend" / "telegram_user"


def _telethon_session_file() -> Path:
    return _telethon_session_base().with_suffix(".session")


def _quarantine_telethon_session(reason: str) -> None:
    base = _telethon_session_base()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for suffix in [".session", ".session-journal", ".session-wal", ".session-shm"]:
        src = Path(f"{base}{suffix}")
        if src.exists():
            dst = src.with_name(f"{src.name}.broken_{stamp}")
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                continue
    monitor_status["last_error"] = f"Telethon session reset: {reason}"


def _get_saved_string_session() -> str | None:
    value = repo.get_setting("telethon.string_session", None)
    if not value:
        return None
    value = value.strip()
    return value or None


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
    claude_model: str | None = DEFAULT_CLAUDE_MODEL
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_bot_token: str | None = None
    telegram_bot_chat_id: str | None = None


class TelethonCodeRequest(BaseModel):
    phone: str


class TelethonCodeVerify(BaseModel):
    phone: str
    code: str
    password: str | None = None


class MonitorConfigPayload(BaseModel):
    collect_enabled: bool
    ai_enabled: bool
    interval_seconds: int = Field(default=MONITOR_INTERVAL_SECONDS, ge=MIN_MONITOR_INTERVAL_SECONDS, le=MAX_MONITOR_INTERVAL_SECONDS)
    fetch_depth: int = Field(default=DEFAULT_MONITOR_DEPTH, ge=MIN_MONITOR_DEPTH, le=MAX_MONITOR_DEPTH)
    ai_prompt: str | None = None


class ClearMessagesPayload(BaseModel):
    confirm: bool = False


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


def _canonical_source_url(username: str) -> str:
    return f"https://t.me/{username.lower()}"


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


def _resolve_claude_model(value: str | None) -> str:
    model = (value or "").strip()
    if model in CLAUDE_MODELS:
        return model
    return DEFAULT_CLAUDE_MODEL


def _detect_media_type(message: object) -> str | None:
    media = getattr(message, "media", None)
    if not media:
        return None
    media_name = media.__class__.__name__.lower()
    if "photo" in media_name:
        return "photo"
    if "document" in media_name:
        return "document"
    return "media"


def _get_monitor_config() -> dict[str, bool | int | str]:
    collect_enabled = (repo.get_setting("monitor.collect_enabled", "1") or "1") == "1"
    ai_enabled = (repo.get_setting("monitor.ai_enabled", "1") or "1") == "1"
    interval_raw = repo.get_setting("monitor.interval_seconds", str(MONITOR_INTERVAL_SECONDS)) or str(MONITOR_INTERVAL_SECONDS)
    try:
        interval_seconds = int(interval_raw)
    except ValueError:
        interval_seconds = MONITOR_INTERVAL_SECONDS
    interval_seconds = max(MIN_MONITOR_INTERVAL_SECONDS, min(MAX_MONITOR_INTERVAL_SECONDS, interval_seconds))
    depth_raw = repo.get_setting("monitor.fetch_depth", str(DEFAULT_MONITOR_DEPTH)) or str(DEFAULT_MONITOR_DEPTH)
    try:
        fetch_depth = int(depth_raw)
    except ValueError:
        fetch_depth = DEFAULT_MONITOR_DEPTH
    fetch_depth = max(MIN_MONITOR_DEPTH, min(MAX_MONITOR_DEPTH, fetch_depth))
    ai_prompt = (repo.get_setting("monitor.ai_prompt", "") or "").strip()
    return {
        "collect_enabled": collect_enabled,
        "ai_enabled": ai_enabled,
        "interval_seconds": interval_seconds,
        "fetch_depth": fetch_depth,
        "ai_prompt": ai_prompt,
    }


def _telethon_client_init_data(api_id: int, api_hash: str) -> tuple[str, int, str]:
    string_session = _get_saved_string_session()
    if string_session:
        return ("string", api_id, api_hash)
    return ("file", api_id, api_hash)


async def _sync_sources_last_messages() -> tuple[int, int, int, str | None]:
    monitor_cfg = _get_monitor_config()
    if not monitor_cfg["collect_enabled"]:
        return 0, 0, 0, "Збір повідомлень глобально вимкнений у вкладці Моніторинг"
    fetch_depth = int(monitor_cfg["fetch_depth"])

    integrations = repo.get_integrations()
    api_id = (integrations.get("telegram_api_id") or "").strip()
    api_hash = (integrations.get("telegram_api_hash") or "").strip()
    if not re.fullmatch(r"\d{5,12}", api_id) or not re.fullmatch(
        r"[a-fA-F0-9]{32}", api_hash
    ):
        return 0, 0, 0, "Telegram User API ID/Hash не заповнені або некоректні"

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        return 0, 0, 0, "Telethon не встановлено (виконайте: pip install -r backend/requirements.txt)"

    sources = repo.list_sources(sort_by="alpha")
    session_path = _telethon_session_base()
    updated = 0
    ingested = 0
    window_start = datetime.now(timezone.utc) - timedelta(seconds=int(monitor_cfg["interval_seconds"]))
    async with telethon_client_lock:
        try:
            client_mode, parsed_api_id, parsed_api_hash = _telethon_client_init_data(int(api_id), api_hash)
            session_obj = StringSession(_get_saved_string_session()) if client_mode == "string" else str(session_path)
            client = TelegramClient(session_obj, parsed_api_id, parsed_api_hash)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    return 0, len(sources), 0, "Telethon-сесія не авторизована (потрібен login)"
                for source in sources:
                    if not source.get("is_active"):
                        continue
                    username = _extract_telegram_username(source["url"] or "")
                    if not username:
                        continue
                    try:
                        entity = await client.get_entity(username)
                        latest = await client.get_messages(entity, limit=1)
                        if latest and latest[0] and latest[0].date:
                            dt_utc = latest[0].date.astimezone(timezone.utc)
                            repo.update_source_last_message(
                                int(source["id"]),
                                dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                            )
                            updated += 1

                        last_known_id = repo.get_last_tg_message_id(int(source["id"]))
                        candidates: list[object] = []
                        async for message in client.iter_messages(entity, limit=fetch_depth):
                            if not message:
                                continue
                            message_id = int(getattr(message, "id", 0))
                            msg_date = getattr(message, "date", None)
                            if message_id <= 0 or msg_date is None:
                                continue
                            msg_date_utc = msg_date.astimezone(timezone.utc)
                            if message_id <= last_known_id:
                                continue
                            if msg_date_utc < window_start:
                                continue
                            candidates.append(message)

                        for message in reversed(candidates):
                            message_id = int(getattr(message, "id", 0))
                            msg_date = getattr(message, "date", None)
                            if message_id <= 0 or msg_date is None:
                                continue
                            msg_date_utc = msg_date.astimezone(timezone.utc)
                            text = (
                                getattr(message, "message", None)
                                or getattr(message, "raw_text", None)
                                or getattr(message, "text", None)
                                or ""
                            )
                            repo.upsert_message(
                                source_id=int(source["id"]),
                                tg_message_id=message_id,
                                published_at=msg_date_utc.strftime("%Y-%m-%d %H:%M:%S"),
                                text=text,
                                media_type=_detect_media_type(message),
                                telegram_url=f"https://t.me/{username}/{message_id}",
                                raw_json=json.dumps(message.to_dict(), ensure_ascii=False, default=str),
                                enqueue_ai=monitor_cfg["ai_enabled"] and bool(source.get("ai_enabled")),
                            )
                            repo.update_source_last_message(
                                int(source["id"]),
                                msg_date_utc.strftime("%Y-%m-%d %H:%M:%S"),
                            )
                            ingested += 1
                    except Exception:
                        continue
            finally:
                await client.disconnect()
        except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            return 0, len(sources), 0, f"Session DB помилка: {exc}"
    return updated, len(sources), ingested, None


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
            monitor_cfg = _get_monitor_config()
            monitor_status["interval_seconds"] = int(monitor_cfg["interval_seconds"])
            monitor_status["state"] = "running"
            monitor_status["last_run_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            updated, total, ingested, err = await _sync_sources_last_messages()
            monitor_status["updated_sources"] = updated
            monitor_status["total_sources"] = total
            monitor_status["ingested_messages"] = ingested
            if err:
                monitor_status["state"] = "warning"
                monitor_status["last_error"] = err
            else:
                await _process_ai_queue()
                monitor_status["state"] = "ok"
                monitor_status["last_error"] = None
                monitor_status["last_success_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
        except Exception:
            monitor_status["state"] = "error"
            monitor_status["last_error"] = "Непередбачена помилка моніторингу"
        await asyncio.sleep(int(_get_monitor_config()["interval_seconds"]))


@app.on_event("startup")
async def startup() -> None:
    init_db()
    global monitor_task
    if monitor_task is None:
        monitor_task = asyncio.create_task(_monitor_loop())


@app.get("/api/monitor/status")
def get_monitor_status() -> dict:
    return {**monitor_status, **_get_monitor_config()}


@app.get("/api/monitor/config")
def get_monitor_config() -> dict:
    return _get_monitor_config()


@app.post("/api/monitor/config")
def save_monitor_config(payload: MonitorConfigPayload) -> dict:
    repo.set_setting("monitor.collect_enabled", "1" if payload.collect_enabled else "0")
    repo.set_setting("monitor.ai_enabled", "1" if payload.ai_enabled else "0")
    repo.set_setting("monitor.interval_seconds", str(payload.interval_seconds))
    repo.set_setting("monitor.fetch_depth", str(payload.fetch_depth))
    repo.set_setting("monitor.ai_prompt", (payload.ai_prompt or "").strip())
    return _get_monitor_config()


@app.post("/api/monitor/run-once")
async def run_monitor_once() -> dict:
    updated, total, ingested, err = await _sync_sources_last_messages()
    monitor_status["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    monitor_status["updated_sources"] = updated
    monitor_status["total_sources"] = total
    monitor_status["ingested_messages"] = ingested
    if err:
        monitor_status["state"] = "warning"
        monitor_status["last_error"] = err
    else:
        await _process_ai_queue()
        monitor_status["state"] = "ok"
        monitor_status["last_error"] = None
        monitor_status["last_success_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return {"ok": err is None, "updated_sources": updated, "total_sources": total, "ingested_messages": ingested, "detail": err}


async def _process_ai_queue(limit: int = 20) -> None:
    if ai_processing_lock.locked():
        return
    monitor_cfg = _get_monitor_config()
    if not monitor_cfg["ai_enabled"]:
        return
    integrations = repo.get_integrations()
    api_key = (integrations.get("claude_api_key") or "").strip()
    if not api_key:
        return
    model = _resolve_claude_model(integrations.get("claude_model"))
    categories = [c.get("name", "").strip() for c in repo.list_categories() if c.get("name")]
    ai_prompt = str(monitor_cfg.get("ai_prompt") or "").strip()

    async with ai_processing_lock:
        pending = repo.claim_ai_queue_pending(limit=limit)
        if not pending:
            return
        loop = asyncio.get_running_loop()
        for item in pending:
            message_id = int(item.get("message_id") or 0)
            text = (item.get("text") or "").strip()
            if message_id <= 0 or not text:
                repo.mark_ai_error(message_id, "empty message text")
                continue
            try:
                score, category = await loop.run_in_executor(
                    None,
                    _call_claude_score_sync,
                    api_key,
                    model,
                    _prepare_ai_text(text),
                    categories,
                    ai_prompt,
                )
                repo.mark_ai_result(message_id, score, category)
            except Exception as exc:
                repo.mark_ai_error(message_id, str(exc))


def _prepare_ai_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    title = lines[0] if lines else ""
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    first_paragraph = paragraphs[0] if paragraphs else title
    reduced = f"Заголовок: {title}\nПерший абзац: {first_paragraph}".strip()
    return reduced[:1600]


def _call_claude_score_sync(
    api_key: str,
    model: str,
    text: str,
    categories: list[str],
    custom_prompt: str,
) -> tuple[int, str | None]:
    categories_text = ", ".join(categories) if categories else "Без категорії"
    base_prompt = (
        custom_prompt
        or "Оціни медіа-важливість повідомлення від 1 до 10 і обери найкращу категорію."
    )
    system_prompt = (
        f"{base_prompt}\n"
        f"Категорії: {categories_text}.\n"
        "Поверни ТІЛЬКИ JSON без пояснень, формат: {\"score\": 7, \"category\": \"Економіка\"}."
    )
    body = {
        "model": model,
        "max_tokens": 120,
        "system": [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
                ],
            }
        ],
    }
    req = request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=25) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = raw.get("content") or []
    text_payload = ""
    if content and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_payload += str(block.get("text") or "")
    payload = text_payload.strip()
    try:
        parsed = json.loads(payload or "{}")
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", payload, flags=re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
    score = int(parsed.get("score") or 0)
    score = max(1, min(10, score))
    category = str(parsed.get("category") or "").strip() or None
    if categories and category not in categories:
        category = None
    return score, category


@app.get("/api/telethon/auth/status")
async def telethon_auth_status() -> dict:
    api_id, api_hash = _telethon_client_config()
    session_name = "telegram_user"
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telethon не встановлено. Виконайте: pip install -r backend/requirements.txt",
        ) from exc
    session_path = _telethon_session_base()
    async with telethon_client_lock:
        try:
            mode, parsed_api_id, parsed_api_hash = _telethon_client_init_data(api_id, api_hash)
            session_obj = StringSession(_get_saved_string_session()) if mode == "string" else str(session_path)
            client = TelegramClient(session_obj, parsed_api_id, parsed_api_hash)
            try:
                await client.connect()
                authorized = await client.is_user_authorized()
            finally:
                await client.disconnect()
        except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            _quarantine_telethon_session(str(exc))
            return {
                "authorized": False,
                "session": session_name,
                "detail": f"Telethon session file пошкоджений або заблокований: {exc}",
            }
    return {"authorized": authorized, "session": session_name, "detail": None}


@app.get("/api/telethon/session/health")
async def telethon_session_health() -> dict:
    session_name = "telegram_user"
    session_path = _telethon_session_file()
    has_string_session = bool(_get_saved_string_session())
    data: dict[str, object] = {
        "session": session_name,
        "path": str(session_path),
        "exists": session_path.exists(),
        "string_session_exists": has_string_session,
        "size_bytes": session_path.stat().st_size if session_path.exists() else 0,
        "writable": session_path.parent.exists() and session_path.parent.is_dir(),
        "ok": True,
        "detail": "Session storage виглядає коректним",
    }

    if not session_path.exists() and not has_string_session:
        data["detail"] = "Session file/string_session ще не створено (це нормально до першого login)"
        return data

    def _sqlite_check() -> str | None:
        if not session_path.exists():
            return None
        try:
            with sqlite3.connect(f"file:{session_path}?mode=ro", uri=True, timeout=1) as conn:
                conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                pragma = conn.execute("PRAGMA quick_check").fetchone()
                if pragma and pragma[0] != "ok":
                    return f"Session DB може бути пошкоджена: {pragma[0]}"
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "locked" in message.lower():
                return "Session DB зараз заблокована іншим процесом"
            return f"Session DB недоступна: {message}"
        except sqlite3.DatabaseError as exc:
            return f"Session DB пошкоджена: {exc}"
        return None

    loop = asyncio.get_running_loop()
    sqlite_error = await loop.run_in_executor(None, _sqlite_check)
    if sqlite_error:
        data["ok"] = False
        data["detail"] = sqlite_error
        return data

    try:
        integrations = repo.get_integrations()
        api_id_raw = (integrations.get("telegram_api_id") or "").strip()
        api_hash = (integrations.get("telegram_api_hash") or "").strip()
        if data["ok"] and re.fullmatch(r"\d{5,12}", api_id_raw) and re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
            from telethon import TelegramClient  # type: ignore
            from telethon.sessions import StringSession  # type: ignore

            async def _probe() -> None:
                mode, parsed_api_id, parsed_api_hash = _telethon_client_init_data(int(api_id_raw), api_hash)
                session_obj = StringSession(_get_saved_string_session()) if mode == "string" else str(_telethon_session_base())
                client = TelegramClient(session_obj, parsed_api_id, parsed_api_hash)
                try:
                    await client.connect()
                    await client.is_user_authorized()
                finally:
                    await client.disconnect()

            async with telethon_client_lock:
                await _probe()
    except Exception as exc:
        data["ok"] = False
        data["detail"] = f"Session file проходить SQLite check, але Telethon probe впав: {exc}"
    return data


@app.post("/api/telethon/auth/request-code")
async def telethon_request_code(payload: TelethonCodeRequest) -> dict:
    phone = payload.phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("00"):
        phone = f"+{phone[2:]}"
    if not re.fullmatch(r"\+\d{10,15}", phone):
        raise HTTPException(status_code=400, detail="Невірний формат телефону. Використай міжнародний формат, наприклад +380...")
    api_id, api_hash = _telethon_client_config()
    session_name = "telegram_user"
    try:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError, PhoneNumberBannedError, PhoneNumberInvalidError
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telethon не встановлено. Виконайте: pip install -r backend/requirements.txt",
        ) from exc
    try:
        async with telethon_client_lock:
            for attempt in range(3):
                try:
                    login_session = StringSession()
                    client = TelegramClient(login_session, api_id, api_hash)
                    try:
                        await client.connect()
                        if await client.is_user_authorized():
                            return {
                                "ok": True,
                                "detail": "Сесію вже авторизовано. Код підтвердження не потрібен.",
                                "phone": phone,
                            }
                        try:
                            sent = await asyncio.wait_for(client.send_code_request(phone), timeout=25)
                            telethon_auth_state[phone] = {
                                "phone_code_hash": sent.phone_code_hash,
                                "session": login_session.save(),
                            }
                            break
                        except PhoneNumberInvalidError as exc:
                            raise HTTPException(status_code=400, detail="Telegram не приймає цей номер телефону") from exc
                        except PhoneNumberBannedError as exc:
                            raise HTTPException(status_code=400, detail="Цей номер заблоковано в Telegram") from exc
                        except FloodWaitError as exc:
                            raise HTTPException(status_code=429, detail=f"Забагато спроб. Повтори через {exc.seconds} сек.") from exc
                        except Exception as exc:
                            if isinstance(exc, asyncio.TimeoutError):
                                raise HTTPException(status_code=504, detail="Telegram не відповідає. Спробуй ще раз через 10-20 секунд.") from exc
                            if "EOF when reading a line" in str(exc):
                                if attempt == 0:
                                    _quarantine_telethon_session(str(exc))
                                    continue
                                raise HTTPException(status_code=500, detail="Пошкоджена Telethon-сесія. Перевір endpoint /api/telethon/session/health") from exc
                            raise HTTPException(status_code=400, detail=f"Не вдалося надіслати код: {exc}") from exc
                    finally:
                        await client.disconnect()
                except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
                    if attempt == 0:
                        _quarantine_telethon_session(str(exc))
                        continue
                    raise HTTPException(status_code=500, detail=f"Помилка Telethon-сесії: {exc}. Сесію скинуто, повтори запит коду.") from exc
                except Exception as exc:
                    if "EOF when reading a line" in str(exc) and attempt < 2:
                        continue
                    raise HTTPException(status_code=500, detail=f"Помилка Telethon request-code: {exc}") from exc
                break
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Помилка Telethon-сесії: {exc}") from exc
    return {"ok": True, "detail": "Код підтвердження надіслано", "phone": phone}


@app.post("/api/telethon/auth/verify-code")
async def telethon_verify_code(payload: TelethonCodeVerify) -> dict:
    phone = payload.phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("00"):
        phone = f"+{phone[2:]}"
    code = payload.code.strip()
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Вкажіть телефон і код")
    auth_state = telethon_auth_state.get(phone)
    if not auth_state:
        raise HTTPException(status_code=400, detail="Спочатку запитай код підтвердження")
    phone_code_hash = auth_state.get("phone_code_hash")
    login_session = auth_state.get("session")
    if not phone_code_hash or not login_session:
        raise HTTPException(status_code=400, detail="Внутрішня помилка стану авторизації. Запросіть код повторно.")

    api_id, api_hash = _telethon_client_config()
    session_name = "telegram_user"
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            PhoneCodeExpiredError,
            PhoneCodeInvalidError,
            SessionPasswordNeededError,
        )
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telethon не встановлено. Виконайте: pip install -r backend/requirements.txt",
        ) from exc
    async with telethon_client_lock:
        try:
            session_obj = StringSession(login_session)
            client = TelegramClient(session_obj, api_id, api_hash)
            try:
                await client.connect()
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                except PhoneCodeInvalidError as exc:
                    raise HTTPException(status_code=400, detail="Невірний код підтвердження") from exc
                except PhoneCodeExpiredError as exc:
                    telethon_auth_state.pop(phone, None)
                    raise HTTPException(status_code=400, detail="Код прострочений. Запросіть новий код") from exc
                except SessionPasswordNeededError:
                    if not payload.password:
                        raise HTTPException(status_code=400, detail="Потрібен пароль 2FA (Telegram password)")
                    try:
                        await client.sign_in(password=payload.password)
                    except Exception as exc:
                        raise HTTPException(status_code=400, detail="Невірний пароль 2FA") from exc
                except Exception as exc:
                    raise HTTPException(status_code=400, detail=f"Помилка авторизації: {exc}") from exc
                authorized = await client.is_user_authorized()
                if authorized:
                    repo.set_setting("telethon.string_session", client.session.save())
            finally:
                await client.disconnect()
        except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            raise HTTPException(status_code=500, detail=f"Session file помилка: {exc}. Перевір /api/telethon/session/health") from exc
    telethon_auth_state.pop(phone, None)
    return {"ok": authorized, "detail": "Telethon авторизовано" if authorized else "Авторизація не завершена"}


@app.post("/api/telethon/auth/logout")
async def telethon_logout() -> dict:
    async with telethon_client_lock:
        repo.set_setting("telethon.string_session", "")
        telethon_auth_state.clear()
        _quarantine_telethon_session("manual logout")
    return {"ok": True, "detail": "Telethon сесію очищено. Потрібна повторна авторизація."}


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


@app.get("/api/messages")
def list_messages(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(limit, 500))
    return repo.list_messages(limit=safe_limit)


@app.post("/api/messages/clear-all")
def clear_all_messages(payload: ClearMessagesPayload) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Підтвердіть очищення (confirm=true)")
    deleted = repo.clear_all_messages()
    return {"ok": True, "deleted_messages": deleted}


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

    canonical_url = _canonical_source_url(username)
    existing_same_username = [
        src for src in repo.list_sources()
        if (_extract_telegram_username(src.get("url", "")) or "").lower() == username.lower()
    ]
    if existing_same_username:
        raise HTTPException(status_code=409, detail="Source для цього каналу вже існує")
    try:
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


@app.delete("/api/categories/{category_id}", status_code=204)
def delete_category(category_id: int) -> None:
    deleted = repo.delete_category(category_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found")


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


@app.delete("/api/keywords/{keyword_id}", status_code=204)
def delete_keyword(keyword_id: int) -> None:
    deleted = repo.delete_keyword(keyword_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Keyword not found")


@app.get("/api/integrations")
def get_integrations() -> dict:
    data = repo.get_integrations()
    data["claude_model"] = _resolve_claude_model(data.get("claude_model"))
    return data


@app.post("/api/integrations")
def save_integrations(payload: IntegrationsPayload) -> dict:
    data = payload.model_dump()
    clean = {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
    clean["claude_model"] = _resolve_claude_model(clean.get("claude_model"))
    return repo.save_integrations(clean)


@app.post("/api/integrations/validate")
def validate_integrations(payload: IntegrationsPayload) -> dict:
    data = payload.model_dump()
    claude_key = (data.get("claude_api_key") or "").strip()
    claude_model = _resolve_claude_model(data.get("claude_model"))
    telegram_api_id = (data.get("telegram_api_id") or "").strip()
    telegram_api_hash = (data.get("telegram_api_hash") or "").strip()
    telegram_bot_token = (data.get("telegram_bot_token") or "").strip()
    telegram_bot_chat_id = (data.get("telegram_bot_chat_id") or "").strip()

    claude_format = bool(
        re.fullmatch(r"sk-ant-(?:api03-)?[A-Za-z0-9_-]{20,}", claude_key)
    )
    telegram_user_format = bool(
        re.fullmatch(r"\d{5,12}", telegram_api_id)
        and re.fullmatch(r"[a-fA-F0-9]{32}", telegram_api_hash)
    )
    telegram_bot_format = bool(
        re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", telegram_bot_token)
        and re.fullmatch(r"-?(?:100\d{8,}|[1-9]\d{4,})", telegram_bot_chat_id)
    )
    claude_model_ok = claude_model in CLAUDE_MODELS
    claude_ok = claude_format and claude_model_ok
    claude_reason = None if claude_ok else "Очікується ключ формату sk-ant-... і валідна Claude model"
    telegram_user_reason = (
        "API ID/Hash коректні. Фактична перевірка виконується через Telethon авторизацію."
        if telegram_user_format
        else "API ID має бути числом, API Hash — 32 hex-символи"
    )
    telegram_bot_ok = telegram_bot_format
    telegram_bot_reason = None if telegram_bot_ok else "Bot token/chat id не відповідають формату Telegram"
    return {
        "claude": {
            "ok": claude_ok,
            "reason": claude_reason,
            "model": claude_model,
        },
        "telegram_user_api": {
            "ok": telegram_user_format,
            "reason": telegram_user_reason,
        },
        "telethon": {
            "ok": telegram_user_format,
            "reason": None if telegram_user_format else "Для Telethon потрібні коректні Telegram API ID/Hash",
        },
        "telegram_bot_api": {
            "ok": telegram_bot_ok,
            "reason": telegram_bot_reason,
        },
        "overall_ok": claude_ok and telegram_user_format and telegram_bot_ok,
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
