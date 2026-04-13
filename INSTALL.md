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

