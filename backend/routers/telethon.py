from __future__ import annotations

import asyncio
import re
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from config import (
    TELETHON_HEALTH_CACHE_TTL,
    TELETHON_STATUS_CACHE_TTL,
    _telethon_health_cache,
    _telethon_status_cache,
    repo,
    telethon_auth_state,
    telethon_client_lock,
)
from models import TelethonCodeRequest, TelethonCodeVerify
from security import _enforce_telethon_auth_rate_limit, require_admin
from services.telegram import _record_telegram_call
from services.telethon import (
    _chmod_session_files,
    _get_saved_string_session,
    _invalidate_telethon_status_caches,
    _quarantine_telethon_session,
    _telethon_client_config,
    _telethon_client_init_data,
    _telethon_client_kwargs,
    _telethon_session_base,
    _telethon_session_file,
)

router = APIRouter()


@router.get("/api/telethon/auth/status", dependencies=[Depends(require_admin)])
async def telethon_auth_status() -> dict:
    now_mono = time.monotonic()
    cached_value = _telethon_status_cache.get("value")
    cached_at = float(_telethon_status_cache.get("at") or 0.0)
    if isinstance(cached_value, dict) and (now_mono - cached_at) < TELETHON_STATUS_CACHE_TTL:
        return dict(cached_value)

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
            mode, parsed_api_id, parsed_api_hash = _telethon_client_init_data(
                api_id, api_hash
            )
            session_obj = (
                StringSession(_get_saved_string_session())
                if mode == "string"
                else str(session_path)
            )
            client = TelegramClient(
                session_obj,
                parsed_api_id,
                parsed_api_hash,
                **_telethon_client_kwargs(),
            )
            try:
                await client.connect()
                authorized = await client.is_user_authorized()
                _record_telegram_call()
            finally:
                await client.disconnect()
        except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            _quarantine_telethon_session(str(exc))
            result = {
                "authorized": False,
                "session": session_name,
                "detail": f"Telethon session file пошкоджений або заблокований: {exc}",
            }
            _telethon_status_cache["value"] = dict(result)
            _telethon_status_cache["at"] = time.monotonic()
            return result

    result = {"authorized": authorized, "session": session_name, "detail": None}
    _telethon_status_cache["value"] = dict(result)
    _telethon_status_cache["at"] = time.monotonic()
    return result


@router.get("/api/telethon/session/health", dependencies=[Depends(require_admin)])
async def telethon_session_health() -> dict:
    now_mono = time.monotonic()
    cached_value = _telethon_health_cache.get("value")
    cached_at = float(_telethon_health_cache.get("at") or 0.0)
    if isinstance(cached_value, dict) and (now_mono - cached_at) < TELETHON_HEALTH_CACHE_TTL:
        return dict(cached_value)

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
        data["detail"] = (
            "Session file/string_session ще не створено (це нормально до першого login)"
        )
        _telethon_health_cache["value"] = dict(data)
        _telethon_health_cache["at"] = time.monotonic()
        return data

    def _sqlite_check() -> str | None:
        if not session_path.exists():
            return None
        try:
            with sqlite3.connect(
                f"file:{session_path}?mode=ro", uri=True, timeout=1
            ) as conn:
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
        _telethon_health_cache["value"] = dict(data)
        _telethon_health_cache["at"] = time.monotonic()
        return data

    try:
        integrations = repo.get_integrations()
        api_id_raw = (integrations.get("telegram_api_id") or "").strip()
        api_hash = (integrations.get("telegram_api_hash") or "").strip()
        if (
            data["ok"]
            and re.fullmatch(r"\d{5,12}", api_id_raw)
            and re.fullmatch(r"[a-fA-F0-9]{32}", api_hash)
        ):
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            async def _probe() -> None:
                mode, parsed_api_id, parsed_api_hash = _telethon_client_init_data(
                    int(api_id_raw), api_hash
                )
                session_obj = (
                    StringSession(_get_saved_string_session())
                    if mode == "string"
                    else str(_telethon_session_base())
                )
                client = TelegramClient(
                    session_obj,
                    parsed_api_id,
                    parsed_api_hash,
                    **_telethon_client_kwargs(),
                )
                try:
                    await client.connect()
                    await client.is_user_authorized()
                    _record_telegram_call()
                finally:
                    await client.disconnect()

            async with telethon_client_lock:
                await _probe()
    except Exception as exc:
        data["ok"] = False
        data["detail"] = (
            f"Session file проходить SQLite check, але Telethon probe впав: {exc}"
        )

    _telethon_health_cache["value"] = dict(data)
    _telethon_health_cache["at"] = time.monotonic()
    return data


@router.post(
    "/api/telethon/auth/request-code", dependencies=[Depends(require_admin)]
)
async def telethon_request_code(
    payload: TelethonCodeRequest, request: Request
) -> dict:
    phone = payload.phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("00"):
        phone = f"+{phone[2:]}"
    if not re.fullmatch(r"\+\d{10,15}", phone):
        raise HTTPException(
            status_code=400,
            detail="Невірний формат телефону. Використай міжнародний формат, наприклад +380...",
        )
    _enforce_telethon_auth_rate_limit(request, phone)
    api_id, api_hash = _telethon_client_config()
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            FloodWaitError,
            PhoneNumberBannedError,
            PhoneNumberInvalidError,
        )
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
                    client = TelegramClient(
                        login_session,
                        api_id,
                        api_hash,
                        **_telethon_client_kwargs(),
                    )
                    try:
                        await client.connect()
                        if await client.is_user_authorized():
                            _record_telegram_call()
                            return {
                                "ok": True,
                                "detail": "Сесію вже авторизовано. Код підтвердження не потрібен.",
                                "phone": phone,
                            }
                        _record_telegram_call()
                        try:
                            sent = await asyncio.wait_for(
                                client.send_code_request(phone), timeout=25
                            )
                            _record_telegram_call()
                            telethon_auth_state[phone] = {
                                "phone_code_hash": sent.phone_code_hash,
                                "session": login_session.save(),
                            }
                            break
                        except PhoneNumberInvalidError as exc:
                            raise HTTPException(
                                status_code=400,
                                detail="Telegram не приймає цей номер телефону",
                            ) from exc
                        except PhoneNumberBannedError as exc:
                            raise HTTPException(
                                status_code=400,
                                detail="Цей номер заблоковано в Telegram",
                            ) from exc
                        except FloodWaitError as exc:
                            raise HTTPException(
                                status_code=429,
                                detail=f"Забагато спроб. Повтори через {exc.seconds} сек.",
                            ) from exc
                        except Exception as exc:
                            if isinstance(exc, asyncio.TimeoutError):
                                raise HTTPException(
                                    status_code=504,
                                    detail="Telegram не відповідає. Спробуй ще раз через 10-20 секунд.",
                                ) from exc
                            if "EOF when reading a line" in str(exc):
                                if attempt == 0:
                                    _quarantine_telethon_session(str(exc))
                                    continue
                                raise HTTPException(
                                    status_code=500,
                                    detail="Пошкоджена Telethon-сесія. Перевір endpoint /api/telethon/session/health",
                                ) from exc
                            raise HTTPException(
                                status_code=400,
                                detail=f"Не вдалося надіслати код: {exc}",
                            ) from exc
                    finally:
                        await client.disconnect()
                except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
                    if attempt == 0:
                        _quarantine_telethon_session(str(exc))
                        continue
                    raise HTTPException(
                        status_code=500,
                        detail=f"Помилка Telethon-сесії: {exc}. Сесію скинуто, повтори запит коду.",
                    ) from exc
                except Exception as exc:
                    if "EOF when reading a line" in str(exc) and attempt < 2:
                        continue
                    raise HTTPException(
                        status_code=500,
                        detail=f"Помилка Telethon request-code: {exc}",
                    ) from exc
                break
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Помилка Telethon-сесії: {exc}"
        ) from exc
    return {"ok": True, "detail": "Код підтвердження надіслано", "phone": phone}


@router.post(
    "/api/telethon/auth/verify-code", dependencies=[Depends(require_admin)]
)
async def telethon_verify_code(
    payload: TelethonCodeVerify, request: Request
) -> dict:
    phone = payload.phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("00"):
        phone = f"+{phone[2:]}"
    code = payload.code.strip()
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Вкажіть телефон і код")
    _enforce_telethon_auth_rate_limit(request, phone)
    auth_state = telethon_auth_state.get(phone)
    if not auth_state:
        raise HTTPException(
            status_code=400, detail="Спочатку запитай код підтвердження"
        )
    phone_code_hash = auth_state.get("phone_code_hash")
    login_session = auth_state.get("session")
    if not phone_code_hash or not login_session:
        raise HTTPException(
            status_code=400,
            detail="Внутрішня помилка стану авторизації. Запросіть код повторно.",
        )

    api_id, api_hash = _telethon_client_config()
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
            client = TelegramClient(
                session_obj,
                api_id,
                api_hash,
                **_telethon_client_kwargs(),
            )
            try:
                await client.connect()
                try:
                    await client.sign_in(
                        phone=phone, code=code, phone_code_hash=phone_code_hash
                    )
                    _record_telegram_call()
                except PhoneCodeInvalidError as exc:
                    raise HTTPException(
                        status_code=400, detail="Невірний код підтвердження"
                    ) from exc
                except PhoneCodeExpiredError as exc:
                    telethon_auth_state.pop(phone, None)
                    raise HTTPException(
                        status_code=400,
                        detail="Код прострочений. Запросіть новий код",
                    ) from exc
                except SessionPasswordNeededError:
                    if not payload.password:
                        raise HTTPException(
                            status_code=400,
                            detail="Потрібен пароль 2FA (Telegram password)",
                        )
                    try:
                        await client.sign_in(password=payload.password)
                        _record_telegram_call()
                    except Exception as exc:
                        raise HTTPException(
                            status_code=400, detail="Невірний пароль 2FA"
                        ) from exc
                except Exception as exc:
                    raise HTTPException(
                        status_code=400, detail=f"Помилка авторизації: {exc}"
                    ) from exc
                authorized = await client.is_user_authorized()
                _record_telegram_call()
                if authorized:
                    repo.set_setting("telethon.string_session", client.session.save())
                    _chmod_session_files()
                    _invalidate_telethon_status_caches()
            finally:
                await client.disconnect()
        except (EOFError, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Session file помилка: {exc}. Перевір /api/telethon/session/health",
            ) from exc

    telethon_auth_state.pop(phone, None)
    return {
        "ok": authorized,
        "detail": "Telethon авторизовано" if authorized else "Авторизація не завершена",
    }


@router.post("/api/telethon/auth/logout", dependencies=[Depends(require_admin)])
async def telethon_logout() -> dict:
    async with telethon_client_lock:
        repo.set_setting("telethon.string_session", "")
        telethon_auth_state.clear()
        _quarantine_telethon_session("manual logout")
        _invalidate_telethon_status_caches()
    return {"ok": True, "detail": "Telethon сесію очищено. Потрібна повторна авторизація."}
