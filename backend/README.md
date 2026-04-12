# NewsMon Prototype API (Sources / Categories / Keywords)

## Run

```bash
cd backend
uvicorn app:app --reload
```

SQLite database file is created automatically at `backend/newsmon.db`.

## Endpoints

- `GET /api/sources`
- `POST /api/sources`
- `GET /api/categories`
- `POST /api/categories`
- `GET /api/keywords`
- `POST /api/keywords`
