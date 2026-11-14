from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from security import require_admin

router = APIRouter(prefix="/api/system/update", dependencies=[Depends(require_admin)])
_UNIT = "newsmon-update.service"
_SYSTEMCTL = "/usr/bin/systemctl"
_UNIT_FILE = Path("/etc/systemd/system") / _UNIT
_start_lock = asyncio.Lock()


async def _run(*command: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise OSError("systemd command failed")
    return output.decode(errors="replace")


@router.get("")
async def update_status() -> dict:
    if not _UNIT_FILE.is_file():
        return {"status": "unavailable"}
    try:
        output = await _run(
            _SYSTEMCTL, "show", _UNIT, "--property=LoadState,ActiveState,Result,ExecMainStartTimestampMonotonic"
        )
    except (OSError, TimeoutError) as exc:
        raise HTTPException(503, "Не вдалося перевірити стан оновлення.") from exc

    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    started = fields.get("ExecMainStartTimestampMonotonic", "0")
    if fields.get("LoadState") != "loaded":
        status = "unavailable"
    elif fields.get("ActiveState") in {"activating", "deactivating"}:
        status = "running"
    elif fields.get("ActiveState") == "failed" or fields.get("Result") not in {None, "success"}:
        status = "failed"
    else:
        status = "succeeded" if started != "0" else "idle"
    return {"status": status, "started": started}


@router.post("", status_code=202)
async def start_update() -> dict:
    async with _start_lock:
        previous = await update_status()
        if previous["status"] == "unavailable":
            raise HTTPException(503, "Запусти sudo bash setup.sh, щоб увімкнути оновлення з адмінки.")
        if previous["status"] == "running":
            raise HTTPException(409, "Оновлення NewsMon уже виконується.")
        try:
            await _run("/usr/bin/sudo", "-n", _SYSTEMCTL, "restart", "--no-block", _UNIT)
        except (OSError, TimeoutError) as exc:
            raise HTTPException(503, "Не вдалося запустити оновлення. Перевір налаштування systemd і sudoers.") from exc
        return {"status": "queued", "previous_start": previous["started"]}
