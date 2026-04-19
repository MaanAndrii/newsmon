from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse

import config
from config import PROTOTYPE_DIR
from db import init_db
from services.digest import _digest_loop
from services.monitor import _ai_loop, _monitor_loop

from routers import alerts as alerts_router
from routers import categories as categories_router
from routers import integrations as integrations_router
from routers import keywords as keywords_router
from routers import messages as messages_router
from routers import monitor as monitor_router
from routers import sources as sources_router
from routers import digest as digest_router
from routers import stats as stats_router
from routers import telethon as telethon_router

app = FastAPI(title="NewsMon Prototype API", version="0.1.0")

app.include_router(sources_router.router)
app.include_router(messages_router.router)
app.include_router(categories_router.router)
app.include_router(keywords_router.router)
app.include_router(alerts_router.router)
app.include_router(monitor_router.router)
app.include_router(integrations_router.router)
app.include_router(telethon_router.router)
app.include_router(digest_router.router)
app.include_router(stats_router.router)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    if config.monitor_task is None:
        config.monitor_task = asyncio.create_task(_monitor_loop())
    if config.ai_task is None:
        config.ai_task = asyncio.create_task(_ai_loop())
    if config.digest_task is None:
        config.digest_task = asyncio.create_task(_digest_loop())


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PROTOTYPE_DIR / "dashboard.html")


@app.get("/dashboard.html")
def dashboard_page() -> FileResponse:
    return FileResponse(PROTOTYPE_DIR / "dashboard.html")


@app.get("/settings.html")
def settings_page() -> FileResponse:
    return FileResponse(PROTOTYPE_DIR / "settings.html")


@app.get("/robots.txt")
def robots_txt() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /\n", media_type="text/plain")
