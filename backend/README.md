# NewsMon Prototype API (Sources / Categories / Keywords)

## Run

```bash
cd backend
uvicorn app:app --reload
```

SQLite database file is created automatically at `backend/newsmon.db`.

## Open in browser

- API docs: `http://127.0.0.1:8000/docs`
- Dashboard prototype: `http://127.0.0.1:8000/dashboard.html`
- Settings prototype: `http://127.0.0.1:8000/settings.html`

## Endpoints

- `GET /api/sources`
- `POST /api/sources`
- `GET /api/categories`
- `POST /api/categories`
- `GET /api/keywords`
- `POST /api/keywords`
