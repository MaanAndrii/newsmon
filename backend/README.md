# NewsMon Prototype API (Sources / Categories / Keywords)

## Run

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

SQLite database file is created automatically at `backend/newsmon.db`.

Detailed Raspberry Pi deployment guide: `../INSTALL.md`.

## Open in browser

- API docs: `http://127.0.0.1:8000/docs`
- Dashboard prototype: `http://127.0.0.1:8000/dashboard.html`
- Settings prototype: `http://127.0.0.1:8000/settings.html`

## Endpoints

- `GET /api/sources`
- `POST /api/sources`
- `PATCH /api/sources/{id}`
- `DELETE /api/sources/{id}`
- `GET /api/messages`
- `POST /api/messages/clear-all`
- `GET /api/categories`
- `POST /api/categories`
- `DELETE /api/categories/{id}`
- `GET /api/keywords`
- `POST /api/keywords`
- `DELETE /api/keywords/{id}`
- `GET /api/integrations`
- `POST /api/integrations`
- `POST /api/integrations/validate`
- `GET /api/monitor/status`
- `GET /api/monitor/config`
- `POST /api/monitor/config`
- `POST /api/monitor/run-once`
- `GET /api/telethon/auth/status`
- `GET /api/telethon/session/health`
- `POST /api/telethon/auth/request-code`
- `POST /api/telethon/auth/verify-code`
- `POST /api/telethon/auth/logout`

## Telethon troubleshooting

- Use `GET /api/telethon/session/health` to verify both SQLite integrity and whether a string session is configured.
- Use `POST /api/monitor/run-once` to trigger manual fetch during testing.
- Monitor config supports `interval_seconds` (300..1800) and `fetch_depth` (1..10).
