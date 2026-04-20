# NewsMon — детальна інструкція встановлення на Raspberry Pi

> Оновлено: 12 квітня 2026  
> Ціль: підняти робочий прототип (FastAPI + SQLite + Telethon + Web UI) на Raspberry Pi в локальній мережі.

---

## 1) Вимоги

### Апаратні
- Raspberry Pi 4/5 (рекомендовано 4+ GB RAM).
- SSD через USB 3.0 (бажано) або microSD.
- Стабільне живлення (офіційний БЖ 5V/3A).
- Доступ до локальної мережі та інтернету.

### Програмні
- Raspberry Pi OS Lite (64-bit).
- Python 3.11+.
- Git.
- Доступ до Telegram API:
  - `API ID`
  - `API Hash`
  - номер телефону Telegram-акаунта.

---

## 2) Підготовка системи

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip sqlite3
```

Перевір:
```bash
python3 --version
git --version
```

---

## 3) Клонування репозиторію

```bash
cd /home/maan
git clone https://github.com/MaanAndrii/newsmon.git
cd newsmon
```

> Якщо у тебе інша гілка для розробки, переключись:
```bash
git checkout work
```

---

## 4) Python-оточення і залежності

```bash
cd /home/maan/newsmon/backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5) Перший запуск вручну

```bash
cd /home/maan/newsmon/backend
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

Відкрий у браузері:
- `http://<IP_PI>:8000/dashboard.html`
- `http://<IP_PI>:8000/settings.html`
- `http://<IP_PI>:8000/docs`

На першому старті автоматично створиться SQLite БД:
- `/home/maan/newsmon/backend/newsmon.db`

---

## 6) Налаштування інтеграцій у UI

Відкрий `Налаштування` → `API та інтеграції`:

1. Заповни `Telegram API ID`.
2. Заповни `Telegram API Hash`.
3. Натисни **Зберегти інтеграції**.
4. У блоці Telethon авторизації:
   - введи телефон у форматі `+380...`,
   - натисни **Запросити код**,
   - введи код з Telegram,
   - за потреби введи 2FA пароль,
   - натисни **Підтвердити**.

Додатково перевір:
- `GET /api/telethon/session/health` — стан session-файлу Telethon.

---

## 7) Додавання джерел і запуск ingestion

У вкладці `Джерела`:
1. Додай канал у форматі `@username` або `https://t.me/username`.
2. Переконайся, що `Моніторинг` увімкнений.

Система автоматично в monitor loop:
- перевіряє останні повідомлення джерел,
- оновлює `last_message_at`,
- інжестить останні повідомлення в таблицю `messages`,
- додає нові повідомлення в `ai_queue`.

Дані стрічки Dashboard беруться з `GET /api/messages`.

---

## 8) Налаштування systemd (автозапуск)

Створи сервіс:

```bash
sudo tee /etc/systemd/system/newsmon.service > /dev/null <<'EOF'
[Unit]
Description=NewsMon FastAPI Service
After=network.target

[Service]
Type=simple
User=maan
Group=maan
WorkingDirectory=/home/maan/newsmon/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/maan/newsmon/backend/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Активуй:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now newsmon
sudo systemctl status newsmon
```

Логи:
```bash
journalctl -u newsmon -f
```

---

## 8.1) Адмін-токен `NEWSMON_API_TOKEN` (обов'язково)

Починаючи з phase 1 security, всі адмін-ендпоінти (`/api/integrations`,
`/api/sources` POST/PATCH/DELETE, `/api/categories`, `/api/keywords`,
`/api/monitor/config`, `/api/monitor/run-once`, `/api/messages/clear-all`,
`/api/debug/stats`, всі `/api/telethon/*`) вимагають заголовок
`Authorization: Bearer <NEWSMON_API_TOKEN>`.

Без змінної оточення бекенд повертатиме **503 «Сервер не налаштовано»**,
і сторінка `settings.html` взагалі не відкриється.

### Крок 1. Згенерувати довгий випадковий токен

```bash
openssl rand -hex 32
```

Вивід — це твій адмін-пароль (напр. `9b7d8c...`). Скопіюй і збережи його
в password manager — нічого складнішого тут не треба. Якщо колись
компрометується — просто переусталиш і рестартнеш сервіс.

### Крок 2. Записати токен у захищений env-файл

```bash
sudo install -m 600 -o maan -g maan /dev/null /etc/newsmon.env
echo 'NEWSMON_API_TOKEN=ВСТАВ_СЮДИ_ЗГЕНЕРОВАНИЙ_ТОКЕН' | sudo tee /etc/newsmon.env > /dev/null
sudo chmod 600 /etc/newsmon.env
sudo chown maan:maan /etc/newsmon.env
```

> Права `600` важливі: токен не повинен бути читабельним нікому, крім
> юзера, під яким працює `newsmon.service`. `ps -ef` і `systemctl show`
> при такому підході також НЕ показують значення.

### Крок 3. Підключити env-файл до systemd-сервісу

Варіант через `override` (найчистіший, не чіпає основний unit):

```bash
sudo systemctl edit newsmon
```

У редакторі додати рівно цей блок і зберегти:

```ini
[Service]
EnvironmentFile=/etc/newsmon.env
```

Альтернативно — відредагувати прямо `newsmon.service` з секції 8 і додати
той самий рядок `EnvironmentFile=/etc/newsmon.env` у блок `[Service]`.

### Крок 4. Перезавантажити systemd і рестартнути сервіс

```bash
sudo systemctl daemon-reload
sudo systemctl restart newsmon
sudo systemctl status newsmon --no-pager
```

### Крок 5. Перевірити, що процес бачить токен

```bash
PID=$(pgrep -f 'uvicorn app:app' | head -1)
sudo tr '\0' '\n' < /proc/$PID/environ | grep NEWSMON_API_TOKEN
```

Має вивести `NEWSMON_API_TOKEN=9b7d8c...`. Якщо нічого не вивело —
`EnvironmentFile` не підхопився (перевір права, шлях, `daemon-reload`).

### Крок 6. Перевірити API

```bash
# без токена → 401
curl -i http://127.0.0.1:8000/api/integrations

# з токеном → 200 і JSON із masked-прев'ю секретів
curl -i -H "Authorization: Bearer ВСТАВ_СЮДИ_ТОКЕН" http://127.0.0.1:8000/api/integrations

# публічний дашборд має працювати БЕЗ токена
curl -i http://127.0.0.1:8000/api/messages?limit=3
```

Якщо отримуєш `503 "Сервер не налаштовано: змінна оточення
NEWSMON_API_TOKEN не задана"` — змінну не бачить саме Python-процес
uvicorn. Найчастіші причини:
1. Забув `sudo systemctl daemon-reload` або `restart` після edit.
2. `EnvironmentFile` вказує не на той шлях або має неправильні права
   (systemd тихо ігнорує нечитабельний файл).
3. Раніше був ручний запуск uvicorn і він ще висить — `pkill -f
   'uvicorn app:app'` і далі тільки через `systemctl`.

### Крок 7. Ввести токен у браузері

Відкрий `http://<IP_PI>:8000/settings.html` — при першому відкритті з'явиться
`prompt()` «Введіть адмін-токен». Введи той самий рядок, що й у
`/etc/newsmon.env`. Токен зберігається в `localStorage` браузера (тільки
на твоєму девайсі); колеги, які користуються лише `dashboard.html`,
нічого не вводять і взагалі не знають про існування токена.

Для виходу — кнопка **«Вихід адміна»** у хедері settings: вона очищує
локальний токен і редіректить на дашборд.

### Ротація токена

```bash
openssl rand -hex 32                                    # згенерувати новий
sudo nano /etc/newsmon.env                              # замінити значення
sudo systemctl restart newsmon                          # перезапустити бекенд
```

Усі браузери, що мали старий токен, при наступному запиті отримають 401
і фронт попросить ввести новий. localStorage не треба чистити вручну —
це робиться автоматично.

---

## 9) Базовий health-check після деплою

```bash
curl http://127.0.0.1:8000/api/monitor/status
curl http://127.0.0.1:8000/api/messages?limit=5
curl http://127.0.0.1:8000/api/telethon/auth/status
curl http://127.0.0.1:8000/api/telethon/session/health
```

Очікування:
- `monitor/status` повертає `state`, `updated_sources`, `ingested_messages`.
- `api/messages` повертає список збережених повідомлень.
- `telethon/session/health` має `ok: true` або зрозумілий `detail`.

---

## 10) Оновлення проєкту

```bash
cd /home/maan/newsmon
git pull --rebase
cd backend
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart newsmon
sudo systemctl status newsmon
```

---

## 11) Резервне копіювання БД

```bash
mkdir -p /home/maan/newsmon/backups
cp /home/maan/newsmon/backend/newsmon.db /home/maan/newsmon/backups/newsmon_$(date +%F_%H-%M-%S).db
```

Для відновлення:
```bash
cp /home/maan/newsmon/backups/<backup_file>.db /home/maan/newsmon/backend/newsmon.db
sudo systemctl restart newsmon
```

---

## 12) Типові проблеми

### 1. `Telethon не встановлено`
```bash
cd /home/maan/newsmon/backend
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. `Помилка Telethon-сесії ... EOF when reading a line`
1. Перевір `GET /api/telethon/session/health`.
2. Повтори `Запросити код` після скидання/пересоздання сесії.

### 3. Dashboard порожній
- Перевір, чи є активні джерела.
- Перевір Telethon авторизацію.
- Перевір `GET /api/messages?limit=5`.
- Перевір `journalctl -u newsmon -f`.

### 4. `Пакет openai не встановлено` (Grok або Gemini API)

Пакет `openai>=1.0` потрібен для роботи з Grok (xAI) та Gemini (Google) через
OpenAI-сумісний API. Він вже включений у `requirements.txt`, але може бути
відсутнім, якщо venv було створено до його додавання.

```bash
cd /home/maan/newsmon/backend
source .venv/bin/activate
pip install openai>=1.0
# або перевстановити всі залежності:
pip install -r requirements.txt
sudo systemctl restart newsmon
```

---

## 13) Telethon не аутентифікується (EOF / readonly) — команди порядково

> Виконуй **по черзі**, не пропускаючи кроки.

### Крок 1. Перейти в проєкт і оновити код
```bash
cd /home/maan/newsmon
git checkout work
git pull --rebase
```

### Крок 2. Активувати venv і перевстановити залежності
```bash
cd /home/maan/newsmon/backend
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Крок 3. Повністю видалити старі Telethon session-файли
```bash
cd /home/maan/newsmon/backend
rm -f telegram_user.session
rm -f telegram_user.session-journal
rm -f telegram_user.session-wal
rm -f telegram_user.session-shm
rm -f telegram_user.session.broken_*
```

### Крок 4. Перевірити і виправити права доступу (щоб не було readonly)
```bash
cd /home/maan/newsmon
sudo chown -R maan:maan /home/maan/newsmon
chmod 755 /home/maan/newsmon/backend
find /home/maan/newsmon/backend -type f -name "telegram_user.session*" -exec chmod 600 {} \;
```

### Крок 5. Перезапустити сервіс і перевірити статус
```bash
sudo systemctl daemon-reload
sudo systemctl restart newsmon
sudo systemctl status newsmon --no-pager
```

### Крок 6. Перевірити API health локально
```bash
curl -s http://127.0.0.1:8000/api/telethon/session/health | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/telethon/auth/status | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/monitor/status | python3 -m json.tool
```

### Крок 7. Відкрити UI і пройти авторизацію
1. Відкрий `http://<IP_PI>:8000/settings.html`
2. Введи номер у форматі `+380...`
3. Натисни **Запросити код**
4. Введи код і натисни **Підтвердити**

### Крок 8. Якщо знову є помилка — дивитись live-логи
```bash
journalctl -u newsmon -f
```

Додатково (серверний debug-файл Telethon):
```bash
tail -n 200 /home/maan/newsmon/backend/telethon_debug.log
```
