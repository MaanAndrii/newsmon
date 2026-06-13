# NewsMon Backend

FastAPI + SQLite бекенд для системи моніторингу Telegram-каналів.

## Запуск для розробки

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
NEWSMON_API_TOKEN=dev uvicorn app:app --reload
```

БД створюється автоматично: `backend/newsmon.db`

## Адреси

- Dashboard: `http://127.0.0.1:8000/dashboard.html`
- Адмінка: `http://127.0.0.1:8000/settings.html`
- API docs: `http://127.0.0.1:8000/docs`

## Деплой на Raspberry Pi

```bash
sudo bash setup.sh        # встановлення
sudo bash setup.sh --update    # оновлення
```

Детально → [../INSTALL.md](../INSTALL.md)

## Структура

```
backend/
├── app.py            # FastAPI app, lifespan, монтування static
├── db.py             # SQLite схема, міграції, всі запити
├── config.py         # Константи, in-memory черги та події
├── security.py       # Bearer-токен авторизація, rate limiting
├── models.py         # Pydantic моделі запитів
├── utils.py          # Допоміжні функції
├── requirements.txt
├── routers/
│   ├── alerts.py
│   ├── categories.py
│   ├── digest.py
│   ├── export_import.py  # Імпорт/Експорт + timezone налаштування
│   ├── integrations.py
│   ├── keywords.py
│   ├── messages.py
│   ├── monitor.py        # Конфіг, debug stats, AI-логи
│   ├── sources.py
│   ├── sse.py            # Server-Sent Events
│   ├── stats.py
│   └── telethon.py
└── services/
    ├── monitor.py        # Цикл збору повідомлень (_monitor_loop)
    ├── digest.py         # Цикл дайджесту (_digest_loop)
    ├── claude.py         # Anthropic Claude API
    ├── telegram.py       # Telegram Bot API (алерти)
    ├── telethon.py       # Telethon user client
    ├── lemmatizer.py     # Ukrainian pymorphy3 лематизація
    └── providers/
        ├── __init__.py   # Фабрика провайдерів
        ├── base.py       # ScoreResult / DigestResult dataclasses
        └── openai_compat.py  # Grok / Gemini / DeepSeek через OpenAI SDK
```

## Змінні оточення

| Змінна | Обов'язкова | Опис |
|---|---|---|
| `NEWSMON_API_TOKEN` | ✅ | Адмін Bearer-токен. Без неї бекенд повертає 503. |

Решта конфігурації (API ключі, моделі, налаштування) зберігається в SQLite через веб-інтерфейс.

## API ендпоінти

### Публічні (без токена)
- `GET /api/messages` — стрічка повідомлень
- `GET /api/monitor/status` — стан моніторингу
- `GET /api/categories` — категорії

### Адмінські (потрібен Bearer токен)
- `GET/POST /api/integrations` — API ключі та моделі
- `POST /api/integrations/validate` — перевірка підключень
- `GET/POST /api/monitor/config` — налаштування моніторингу
- `GET/POST /api/digest/config` — налаштування дайджесту
- `POST /api/digest/generate` — ручна генерація дайджесту
- `GET/POST /api/sources` — управління джерелами
- `GET/POST /api/categories` — управління категоріями
- `GET/POST /api/keywords` — управління ключовими словами
- `GET/POST /api/alerts` — управління алертами
- `GET /api/debug/stats` — статистика AI/Telegram запитів
- `GET /api/debug/ai-logs` — останні 10 сирих AI запитів/відповідей
- `GET /api/export/settings` — експорт налаштувань (JSON)
- `GET /api/export/backup` — повний бекап БД
- `POST /api/import/settings` — імпорт налаштувань
- `POST /api/import/backup` — відновлення з бекапу
- `GET/POST /api/settings/timezone` — часовий пояс
- `GET /api/telethon/auth/status` — стан авторизації
- `GET /api/telethon/session/health` — здоров'я сесії
- `POST /api/telethon/auth/request-code` — запит SMS-коду
- `POST /api/telethon/auth/verify-code` — підтвердження коду
- `POST /api/telethon/auth/logout` — вихід із Telethon
