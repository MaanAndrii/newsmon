from __future__ import annotations

import asyncio
import logging
from base64 import b64encode
from dataclasses import dataclass, field
from io import BytesIO
from uuid import uuid4

from fastapi import HTTPException

from config import repo, telethon_client_lock
from services.telegram import _record_telegram_call
from services.telethon import _invalidate_telethon_status_caches, _telethon_client_config


@dataclass
class QRLoginAttempt:
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "starting"
    detail: str = ""
    image: str | None = None
    passwords: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None

    def response(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "detail": self.detail,
            "image": self.image,
        }


qr_login_lock = asyncio.Lock()
_attempt: QRLoginAttempt | None = None
logger = logging.getLogger(__name__)


def get_qr_login(attempt_id: str) -> QRLoginAttempt:
    if _attempt is None or _attempt.id != attempt_id:
        raise HTTPException(404, "Спробу входу завершено. Створіть новий QR-код.")
    return _attempt


async def stop_qr_login() -> None:
    global _attempt
    attempt, _attempt = _attempt, None
    if attempt and attempt.task and not attempt.task.done():
        attempt.task.cancel()
        await asyncio.gather(attempt.task, return_exceptions=True)


async def start_qr_login() -> QRLoginAttempt:
    global _attempt
    api_id, api_hash = _telethon_client_config()
    await stop_qr_login()
    attempt = _attempt = QRLoginAttempt()
    attempt.task = asyncio.create_task(_run_qr_login(attempt, api_id, api_hash))
    return attempt


async def _run_qr_login(attempt: QRLoginAttempt, api_id: int, api_hash: str) -> None:
    client = None
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
        from telethon import TelegramClient
        from telethon.errors import (
            AuthTokenExpiredError,
            PasswordHashInvalidError,
            SessionPasswordNeededError,
        )
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), api_id, api_hash)
        async with asyncio.timeout(25):
            await client.connect()
            login = await client.qr_login()
            _record_telegram_call()

        image = BytesIO()
        qrcode.make(login.url, image_factory=SvgPathImage).save(image)
        try:
            attempt.image = "data:image/svg+xml;base64," + b64encode(image.getvalue()).decode("ascii")
            attempt.status = "scan"
            async with asyncio.timeout(90):
                await login.wait()
            _record_telegram_call()
            attempt.image = None
            attempt.status = "saving"
        except SessionPasswordNeededError:
            attempt.image = None
            attempt.status = "password"
            attempt.detail = "Введіть пароль двоетапної перевірки Telegram."
            async with asyncio.timeout(180):
                while True:
                    password = await attempt.passwords.get()
                    try:
                        await client.sign_in(password=password)
                        _record_telegram_call()
                        attempt.status = "saving"
                        break
                    except PasswordHashInvalidError:
                        attempt.detail = "Невірний пароль двоетапної перевірки."
                        attempt.status = "password"
                    finally:
                        del password
        except AuthTokenExpiredError as exc:
            raise TimeoutError from exc

        async with telethon_client_lock:
            if _telethon_client_config() != (api_id, api_hash):
                attempt.status = "error"
                attempt.detail = "Telegram API ID/Hash змінилися. Створіть новий QR-код."
                return
            repo.set_setting("telethon.string_session", client.session.save())
            _invalidate_telethon_status_caches()
        attempt.status = "authorized"
        attempt.detail = "Акаунт Telegram підключено."
    except ImportError:
        attempt.status = "error"
        attempt.detail = "Встановіть залежності з backend/requirements.txt."
    except TimeoutError:
        attempt.status = "expired"
        attempt.detail = "Час на вхід минув. Створіть новий QR-код."
    except Exception as exc:
        logger.warning("Telegram QR login failed (%s)", type(exc).__name__)
        attempt.status = "error"
        attempt.detail = "Не вдалося завершити вхід через Telegram. Спробуйте ще раз."
    finally:
        attempt.image = None
        if not attempt.passwords.empty():
            attempt.passwords.get_nowait()
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:
                logger.warning("Telegram QR client disconnect failed (%s)", type(exc).__name__)
