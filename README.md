# NewsMon

Система моніторингу Telegram-каналів з AI-оцінкою важливості та автоматичними дайджестами.

---

## Що це

NewsMon збирає повідомлення з Telegram-каналів через Telethon, оцінює їх важливість за допомогою AI (Claude, Grok, Gemini або DeepSeek) і відображає результати у веб-інтерфейсі. Щодня може надсилати автоматичний дайджест у Telegram.

**Для кого:** медіа-аналітики, PR-команди, журналісти, дослідники — всі, хто відстежує велику кількість Telegram-каналів.

---

## Можливості

- **Моніторинг каналів** — автоматичний збір повідомлень через Telegram Telethon API
- **AI-оцінка** — кожне повідомлення отримує оцінку важливості від 1 до 10 та категорію
- **Фільтрація** — ключові слова, категорії, мінімальний рейтинг
- **Алерти** — миттєве сповіщення в Telegram при появі важливого повідомлення
- **Дайджест** — щоденний або за 24 год підсумок у форматі статті, списку або executive summary
- **Дедублікація** — автоматичне виявлення повторюваних повідомлень
- **Підтримка AI-провайдерів** — Anthropic Claude, xAI Grok, Google Gemini, DeepSeek
- **Веб-інтерфейс** — Dashboard + панель адміністратора без окремого фронтенд-білду
- **Налаштований timezone** — відображення часу в адмінці відповідно до конфігурації

---

## Стек

| Компонент | Технологія |
|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn |
| База даних | SQLite (WAL mode) |
| Telegram | Telethon (user account) |
| AI | Anthropic / OpenAI-compatible API |
| Лематизація | pymorphy3 + українські словники |
| Frontend | Vanilla JS, Tailwind CSS (CDN) |
| Розгортання | systemd на Raspberry Pi / Linux |

---

## Швидкий старт (Raspberry Pi / Linux)

```bash
git clone https://github.com/MaanAndrii/newsmon.git
cd newsmon
sudo bash setup.sh
```

Скрипт автоматично:
- встановить системні залежності
- створить Python venv
- налаштує та запустить systemd-сервіс

Після завершення скрипт виведе адресу сервера. Пароль для першого входу в адмінку: `admin`.

Детальна інструкція → [INSTALL.md](INSTALL.md)

---

## Структура

```
newsmon/
├── setup.sh              # Встановлення / оновлення / видалення
├── INSTALL.md            # Детальна інструкція розгортання
├── backend/
│   ├── app.py            # FastAPI додаток, lifespan, роутери
│   ├── db.py             # SQLite схема, міграції, всі запити
│   ├── config.py         # Константи, in-memory стан
│   ├── security.py       # Bearer-токен авторизація
│   ├── requirements.txt
│   ├── routers/          # API ендпоінти по доменах
│   └── services/
│       ├── monitor.py    # Цикл збору повідомлень
│       ├── digest.py     # Цикл генерації дайджесту
│       ├── claude.py     # Anthropic Claude інтеграція
│       ├── telegram.py   # Telegram Bot API (алерти)
│       ├── telethon.py   # Telethon user client
│       ├── lemmatizer.py # Українська лематизація
│       └── providers/    # OpenAI-сумісні провайдери (Grok, Gemini, DeepSeek)
└── prototype/
    ├── dashboard.html    # Публічний дашборд
    └── settings.html     # Панель адміністратора
```

---

## Перша конфігурація

Після запуску сервера відкрий `http://<IP>:8000/settings.html` і пройди:

1. **Інтеграції** — Telegram API ID + Hash, авторизація Telethon
2. **AI-провайдер** — API ключ Claude / Grok / Gemini / DeepSeek + модель
3. **Джерела** — список Telegram-каналів для моніторингу
4. **Категорії** — власні рубрики (Політика, Економіка тощо)
5. **Ключові слова** — фрази та регулярні вирази для фільтрації
6. **Дайджест** — час відправки, формат, мінімальна оцінка

---

## Оновлення

```bash
sudo bash setup.sh --update
```

---

## Корисні команди

```bash
# Логи в реальному часі
journalctl -u newsmon -f

# Статус сервісу
systemctl status newsmon

# Видалити сервіс
sudo bash setup.sh --uninstall
```

---

## API

Повна документація доступна після запуску: `http://<IP>:8000/docs`

Публічні ендпоінти (без токена):
- `GET /api/messages` — стрічка повідомлень
- `GET /api/monitor/status` — стан моніторингу
- `GET /api/categories` — список категорій

Адмін-ендпоінти (потрібен `Authorization: Bearer <пароль>`):
- `POST /api/integrations` — збереження ключів
- `POST /api/monitor/config` — налаштування моніторингу
- `GET /api/debug/stats` — статистика AI-запитів
- та інші — повний список у `/docs`
