# Технічне завдання
## Система моніторингу Telegram-каналів для редакції онлайн-медіа

- **Версія документа:** 2.0
- **Дата складання:** Квітень 2026
- **Статус:** Фінальна редакція
- **Платформа:** Raspberry Pi (Web-інтерфейс)

---

## 1. Загальні відомості

### 1.1 Назва системи
Система моніторингу Telegram-каналів — програмний комплекс для збору, автоматичної ШІ-обробки та аналізу публікацій із Telegram-каналів у реальному часі.

### 1.2 Замовник
Редакція онлайн-медіа (журналісти, редактори, аналітики).

### 1.3 Підстава для розробки
Потреба в оперативному моніторингу Telegram-каналів як ключового джерела новин в Україні.

### 1.4 Ціль і призначення
Надати редакції інструмент для моніторингу десятків каналів з автоматичною оцінкою важливості (0–10) та категоризацією повідомлень.

### 1.5 Ключовий принцип
Автономність: локальне зберігання на Raspberry Pi. Єдина зовнішня залежність — Claude API (з офлайн-чергою).

---

## 2. Технічне середовище

### 2.1 Апаратна платформа
| Параметр | Мінімум | Рекомендовано |
|---|---|---|
| Модель | Raspberry Pi 4 Model B | Raspberry Pi 4 / 5 |
| RAM | 2 ГБ | 4–8 ГБ |
| Накопичувач | 32 ГБ microSD | 64+ ГБ SSD (USB 3.0) |
| ОС | Raspberry Pi OS Lite (64-bit) | Raspberry Pi OS Lite (64-bit) |
| Мережа | Ethernet або Wi‑Fi | Ethernet |
| Живлення | 5В/3А | 5В/3А |

> Рекомендовано SSD замість microSD для надійності БД.

### 2.2 Програмне середовище
| Компонент | Технологія | Призначення |
|---|---|---|
| Бекенд | Python 3.11+ | Основна логіка |
| Telegram-клієнт | Telethon 1.28+ | MTProto API |
| ШІ | Anthropic Claude API | Оцінка і категоризація |
| API | FastAPI 0.110+ | REST + UI backend |
| БД | SQLite 3 + FTS5 | Зберігання та пошук |
| Черга | APScheduler/Celery | Фонові задачі |
| Frontend | HTML5 + Tailwind + Alpine.js | SPA |
| ASGI | Uvicorn | Запуск застосунку |
| Service manager | systemd | Автозапуск |

### 2.3 Мережева архітектура
- Локальна мережа редакції.
- Доступ через статичний IP або DNS (`monitor.redaktsia.local`).
- Веб-інтерфейс: 8080 (HTTP) або 443 (HTTPS).

---

## 3. Функціональні вимоги

### 3.1 Канали
- Додавання за `@username` або посиланням.
- Перевірка існування каналу.
- Збереження метаданих (назва, опис, підписники, аватар).
- Увімкнення/вимкнення моніторингу та ШІ на рівні каналу.
- Масові операції для кількох каналів.

### 3.2 Збір даних
- Початковий імпорт архіву за N днів.
- Постійний збір з інтервалом від 30 сек.
- Обробка редагувань, реакцій, медіа.
- Логування помилок, повторні спроби, відновлення після збоїв.

### 3.3 ШІ-оцінка (AI Scoring Engine)
- Claude повертає `score` (0–10) та `category`.
- Асинхронна обробка у фоні.
- Для нетекстових повідомлень: `score = null`, `category = null`.
- Офлайн-режим: черга `pending` з автоматичною доробкою після відновлення API.
- Ручна корекція оцінки/категорії з прапорцем `manual_override`.

### 3.4 Категорії
- Повний CRUD.
- Поля: назва, колір, опис, ознака дефолтної категорії.
- Видалення категорії переносить повідомлення у дефолтну.

### 3.5 Dashboard
- Картки: канал, дата, score, статус оцінки, категорія, прев’ю тексту.
- Дії: відкрити в Telegram, модальне вікно, «Взяв в роботу», «Опрацьовано», закладка.
- Фільтри: категорія, score, канал, статус workflow, дата.
- Сортування: дата/оцінка.

### 3.6 Status Bar
- Годинник у реальному часі.
- Статуси: Telegram-збір, Claude API, БД.
- Розмір AI-черги, активні/загальні канали.

### 3.7 Пошук
- FTS5-пошук по всіх повідомленнях.
- Фільтри та сортування результатів.
- Saved searches.

### 3.8 Сповіщення
- Правила на ключові слова/regex + канали/категорії/мінімальний score.
- Канали сповіщень: web push, SMTP, Telegram-бот.

### 3.9 Аналітика
- Активність каналів, розподіл категорій/оцінок, топ-канали, типи контенту.

### 3.10 Закладки
- Колекції, нотатки, експорт CSV/JSON/PDF.

---

## 4. Налаштування
Розділи: Канали, Категорії, Ключові слова/Сповіщення, ШІ, API, Користувачі та Ролі, Загальні.

Ролі:
- **Адміністратор** — повний доступ.
- **Редактор** — робота з Dashboard, workflow, ручна корекція.
- **Читач** — лише перегляд.

---

## 5. Нефункціональні вимоги
- Dashboard: < 3 сек.
- Пошук: < 2 сек (до 1 млн повідомлень).
- Поява нових повідомлень: < 60 сек.
- ШІ-оцінка нових повідомлень: < 30 сек.
- Моніторинг: 50–200 каналів.
- RAM у фоні: < 600 МБ.
- CPU idle: < 15%.
- Безпека: bcrypt, ролі доступу, HTTPS, anti-bruteforce, CSRF.

---

## 6. Архітектура системи
- **Data layer:** SQLite + FTS5, файлова система.
- **Business layer:** Telethon Worker, AI Queue, Scheduler, Alert Engine.
- **API layer:** FastAPI + WebSocket.
- **Presentation layer:** SPA.

Основні таблиці: `channels`, `messages`, `messages_fts`, `ai_queue`, `categories`, `media`, `alerts`, `alert_matches`, `bookmarks`, `users`, `settings`.

---

## 7. Веб-інтерфейс
- Розділи: Dashboard, Стрічка, Пошук, Сповіщення, Аналітика, Закладки, Налаштування.
- Dashboard: status bar, фільтри, адаптивна сітка (1/2/3 колонки), infinite scroll.
- Темна тема за замовчуванням.

---

## 8. Інтеграція з Telegram
- Використовується **Telegram User API (MTProto)** через Telethon.
- Потрібні: API ID, API Hash, номер телефону.
- Підтримка FloodWait/Rate limits через черги та повтори.

---

## 9. Конфігурація та розгортання
Ключові `.env` параметри:
`FETCH_INTERVAL`, `INITIAL_HISTORY_DAYS`, `AI_ENABLED`, `AI_MAX_PARALLEL`, `AI_DAILY_TOKEN_LIMIT`, `MAX_DB_SIZE_GB`, `SHOW_NULL_SCORED`, `MEDIA_DOWNLOAD`, `BACKUP_ENABLED`, `BACKUP_TIME`, `RETENTION_DAYS`.

Кроки розгортання:
1. Клонування репозиторію.
2. Запуск `install.sh`.
3. Заповнення `.env`.
4. Перша Telegram-авторизація.
5. Запуск через systemd.
6. Початкове налаштування в UI.

---

## 10. Документація
- `README.md`, `INSTALL.md`, `USER_GUIDE.md`, `ADMIN_GUIDE.md`, `API.md`.
- Вбудовані підказки та сторінка довідки.

---

## 11. План розробки та приймання
- **Черга 1 (MVP):** збір каналів, стрічка, базовий dashboard, авторизація.
- **Черга 2:** AI scoring, категорії, фільтри, status bar.
- **Черга 3:** alerts, workflow, офлайн-черга AI.
- **Черга 4:** аналітика, закладки, ручна корекція, роль читача.
- **Черга 5:** експорт, Telegram-бот, розширений пошук.

Критерії прийняття включають стабільний моніторинг 50+ каналів, SLA за затримками, коректні ролі та відновлення після перезавантаження.

---

## Додаток A. Глосарій
MTProto, Telethon, Claude API, AI Scoring, FTS5, FastAPI, WebSocket, systemd, WAL, Alpine.js, Pending, Workflow.

## Додаток Б. Посилання
- https://docs.telethon.dev
- https://fastapi.tiangolo.com
- https://core.telegram.org/api
- https://docs.anthropic.com
- https://sqlite.org/fts5.html
- https://www.raspberrypi.com/documentation

---

## 12. Delivery-план на 8 тижнів (4 спринти + Sprint 0)

Тривалість спринту: **2 тижні**.  
Формат релізів: **інкрементальний** (кожен спринт завершується deployable-версією).

### 12.0 Sprint 0 (Тиждень 0–1): UI/UX прототип без Raspberry Pi

**Цілі:**
- Спроєктувати інформаційну архітектуру інтерфейсу (Dashboard, Search, Alerts, Settings).
- Реалізувати клікабельний frontend-прототип з mock-даними та `lorem ipsum`.
- Валідувати UX-сценарії редакції до підключення реальних API.
- Працювати локально (ноутбук/десктоп) без етапу встановлення на Raspberry Pi.

**Обсяг робіт:**
- Tailwind + Alpine.js компоненти: картка повідомлення, статус-бар, фільтри, модалка.
- Сторінки: Dashboard, Search, Alerts, Categories, AI Settings.
- Mock API (JSON fixtures) для імітації:
  - `messages` з різними `ai_status` (`done/pending/not_applicable`);
  - `workflow_status` (`new/in_progress/done`);
  - `stats/system` для живого статус-бару.
- Плейсхолдери контенту: `lorem ipsum`, фіктивні канали/категорії/оцінки.

**Endpoint-и прототипу (mock layer):**
- `GET /mock/messages`
- `GET /mock/channels`
- `GET /mock/categories`
- `GET /mock/stats/system`
- `GET /mock/alerts`

**Definition of Done (Sprint 0):**
- Усі ключові екрани зібрані й доступні в браузері локально.
- UX-потоки «перегляд → фільтрація → взяти в роботу → опрацьовано» проходяться без backend.
- Узгоджено візуальний стиль, стани карток та поведінку фільтрів.
- Підготовлено backlog правок по UI перед стартом backend-етапу.

### 12.1 Спринт 1 (Тижні 1–2): Core MVP (інгест + базовий Dashboard)

**Цілі:**
- Базова авторизація (Admin/Editor).
- CRUD каналів + увімкнення/вимкнення моніторингу.
- Збір повідомлень через Telethon.
- Інтеграція готового UI-прототипу Sprint 0 з реальним API.

**Endpoint-и релізу R1:**
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/channels`
- `POST /api/channels`
- `PATCH /api/channels/{id}`
- `DELETE /api/channels/{id}`
- `GET /api/messages`
- `GET /api/stats/system`

**Definition of Done (R1):**
- Можна додати мінімум 20 каналів і отримати повідомлення в БД.
- Нові повідомлення потрапляють у Dashboard не пізніше ніж за 60 секунд.
- Канал можна вмикати/вимикати без падіння worker-процесу.
- Авторизація працює, неавторизований користувач не бачить API дані.
- Сервіс запускається через systemd та відновлюється після рестарту.
- Компоненти зі Sprint 0 підключені до production endpoint-ів без зміни UX-логіки.

---

### 12.2 Спринт 2 (Тижні 3–4): AI Scoring + Категорії + Status Bar

**Цілі:**
- CRUD категорій.
- AI-черга (`pending/processing/done/failed`).
- Інтеграція Claude API для score/category.
- Статуси AI в картках і у статус-барі.

**Endpoint-и релізу R2:**
- `GET /api/categories`
- `POST /api/categories`
- `PUT /api/categories/{id}`
- `DELETE /api/categories/{id}`
- `GET /api/messages`
- `PATCH /api/messages/{id}/score`
- `GET /api/stats/system`
- `POST /api/ai/reindex` (пакетна переоцінка за період)

**Definition of Done (R2):**
- Нові текстові повідомлення отримують score/category (за доступного API).
- При недоступності Claude повідомлення стають у `pending`.
- Після відновлення API `pending` обробляються автоматично (FIFO).
- У Dashboard видно стани: «Оцінено», «Очікує оцінки», «Не оцінено», «Виправлено вручну».
- Є ручний override оцінки/категорії з фіксацією `manual_override=true`.

---

### 12.3 Спринт 3 (Тижні 5–6): Редакційний workflow + Пошук + Сповіщення

**Цілі:**
- Workflow: new → in_progress → done.
- Повнотекстовий пошук (FTS5) + фільтри.
- Alert engine (ключові слова/regex + мінімальний score).
- Канали сповіщень: web + email (SMTP), Telegram-бот як опція.

**Endpoint-и релізу R3:**
- `PATCH /api/messages/{id}/workflow`
- `GET /api/search?q=...`
- `POST /api/alerts`
- `GET /api/alerts`
- `PUT /api/alerts/{id}`
- `DELETE /api/alerts/{id}`
- `GET /api/alerts/matches`
- `WS /ws/live`

**Definition of Done (R3):**
- Редактор може взяти матеріал у роботу та завершити його.
- Видно «мої матеріали» та хто вже працює з повідомленням.
- Пошук по 500k повідомлень відповідає < 2 сек (на індексованій БД).
- Правила сповіщень тригеряться та логуються в `alert_matches`.
- Live-оновлення нових повідомлень та статусів приходять через WebSocket.

---

### 12.4 Спринт 4 (Тижні 7–8): Аналітика + Закладки + Експорт + Hardening

**Цілі:**
- Аналітика (активність каналів, категорії, score-гістограма).
- Закладки/колекції/нотатки.
- Експорт CSV/JSON/PDF.
- Резервні копії, лог-ротація, hardening безпеки.

**Endpoint-и релізу R4:**
- `GET /api/analytics/activity`
- `GET /api/analytics/categories`
- `GET /api/analytics/scores`
- `GET /api/bookmarks`
- `POST /api/bookmarks`
- `PUT /api/bookmarks/{id}`
- `DELETE /api/bookmarks/{id}`
- `GET /api/export`
- `GET /api/settings`
- `PUT /api/settings`

**Definition of Done (R4):**
- Доступні аналітичні графіки для обраного періоду.
- Користувач створює колекцію, додає нотатку, експортує результати.
- Щоденний backup БД створюється за розкладом і відновлюється тестово.
- Налаштування безпеки (брутфорс-ліміт, ролі, CSRF/HTTPS) увімкнені.
- Підготовлено реліз-кандидат для UAT та приймальних тестів.

---

## 13. Мінімальна схема БД (DDL для SQLite)

> Примітка: індекси й тригери можна розширювати у міграціях v2+.

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  login TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','editor','reader')),
  is_active INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login TEXT NULL
);

CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_channel_id TEXT NULL,
  username TEXT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT NULL,
  subscribers_count INTEGER NULL,
  avatar_url TEXT NULL,
  monitor_enabled INTEGER NOT NULL DEFAULT 1,
  ai_enabled INTEGER NOT NULL DEFAULT 1,
  tags_json TEXT NULL,
  last_collected_at TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  color TEXT NOT NULL DEFAULT '#64748b',
  description TEXT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  tg_message_id INTEGER NOT NULL,
  published_at TEXT NOT NULL,
  edited_at TEXT NULL,
  text TEXT NULL,
  message_type TEXT NOT NULL DEFAULT 'text',
  has_media INTEGER NOT NULL DEFAULT 0,
  media_group_id TEXT NULL,
  source_forwarded_from TEXT NULL,
  telegram_link TEXT NULL,
  ai_score INTEGER NULL CHECK(ai_score BETWEEN 0 AND 10),
  ai_category_id INTEGER NULL REFERENCES categories(id) ON DELETE SET NULL,
  ai_status TEXT NOT NULL DEFAULT 'pending' CHECK(ai_status IN ('pending','processing','done','failed','not_applicable')),
  manual_override INTEGER NOT NULL DEFAULT 0,
  workflow_status TEXT NOT NULL DEFAULT 'new' CHECK(workflow_status IN ('new','in_progress','done')),
  in_progress_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
  in_progress_at TEXT NULL,
  processed_at TEXT NULL,
  raw_json TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(channel_id, tg_message_id)
);

CREATE TABLE IF NOT EXISTS ai_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','done','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NULL,
  tokens_used INTEGER NOT NULL DEFAULT 0,
  scheduled_at TEXT NOT NULL DEFAULT (datetime('now')),
  processed_at TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  keyword_pattern TEXT NOT NULL,
  use_regex INTEGER NOT NULL DEFAULT 0,
  min_score INTEGER NULL CHECK(min_score BETWEEN 0 AND 10),
  channels_json TEXT NULL,
  categories_json TEXT NULL,
  notify_web INTEGER NOT NULL DEFAULT 1,
  notify_email INTEGER NOT NULL DEFAULT 0,
  notify_telegram INTEGER NOT NULL DEFAULT 0,
  quiet_hours_from TEXT NULL,
  quiet_hours_to TEXT NULL,
  created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alert_matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  matched_text TEXT NULL,
  delivered_channels_json TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmarks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  collection_name TEXT NULL,
  note TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(message_id, user_id)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL,
  file_name TEXT NULL,
  mime_type TEXT NULL,
  file_size INTEGER NULL,
  local_path TEXT NULL,
  remote_url TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(
  text,
  content='messages',
  content_rowid='id',
  tokenize='unicode61'
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_ai_status ON messages(ai_status);
CREATE INDEX IF NOT EXISTS idx_messages_workflow ON messages(workflow_status);
CREATE INDEX IF NOT EXISTS idx_ai_queue_status_time ON ai_queue(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_alert_matches_alert_time ON alert_matches(alert_id, created_at DESC);
```

---

## 14. DoD рівня релізу (загальний чекліст)

- Пройдені smoke-тести API + UI сценарії.
- Немає blocker/critical багів у трекері.
- Міграції БД і rollback-інструкція перевірені.
- Оновлено `README/INSTALL/USER_GUIDE` для нового функціоналу.
- Зібрані метрики продуктивності (latency API, черга AI, RAM/CPU).
- Підписаний release note та тег релізу в Git.
