# NewsMon — інструкція встановлення на Raspberry Pi

> Оновлено: червень 2026  
> Ціль: підняти робочу систему (FastAPI + SQLite + Telethon + Web UI) на Raspberry Pi в локальній мережі.

---

## Вимоги

### Апаратні
- Raspberry Pi 4 / 5 (рекомендовано 4+ GB RAM)
- SSD через USB 3.0 (бажано) або microSD
- Стабільне живлення (офіційний БЖ 5V/3A)
- Доступ до інтернету

### Програмні
- Raspberry Pi OS Lite 64-bit (Debian Bookworm)
- Python 3.11+
- Git

### Облікові дані (отримай заздалегідь)
- **Telegram API ID + API Hash** — [my.telegram.org](https://my.telegram.org) → API development tools
- **API ключ AI-провайдера** (хоча б один):
  - Claude: [console.anthropic.com](https://console.anthropic.com)
  - Grok: [console.x.ai](https://console.x.ai)
  - Gemini: [aistudio.google.com](https://aistudio.google.com)
  - DeepSeek: [platform.deepseek.com](https://platform.deepseek.com)

---

## 1. Встановлення (автоматичне)

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/MaanAndrii/newsmon.git
cd newsmon
sudo bash setup.sh
```

Скрипт автоматично виконає всі кроки нижче: встановить залежності, створить Python venv, згенерує адмін-токен, налаштує та запустить systemd-сервіс.

Після завершення в терміналі будуть:
- **Адреса** сервера (`http://<IP>:8000`)
- **Адмін-токен** — скопіюй і збережи в надійне місце

---

## 2. Перша конфігурація в браузері

Відкрий `http://<IP Raspberry Pi>:8000/settings.html`

При першому відкритті з'явиться вікно авторизації — введи адмін-токен, який вивів `setup.sh`.

### 2.1 Telegram API + Telethon авторизація

Вкладка **«API та інтеграції»**:

1. Введи `Telegram API ID` та `Telegram API Hash`
2. Натисни **Зберегти інтеграції**
3. У блоці Telethon:
   - Введи номер телефону у форматі `+380...`
   - Натисни **Запросити код**
   - Введи код із Telegram
   - За потреби введи 2FA пароль
   - Натисни **Підтвердити**

### 2.2 AI-провайдер

У тій само вкладці обери та налаштуй один провайдер:

| Провайдер | Рекомендована модель | Поле API Key |
|---|---|---|
| Claude (Anthropic) | `claude-haiku-4-5-20251001` | Claude API Key |
| Grok (xAI) | `grok-3-mini` | Grok API Key |
| Gemini (Google) | `gemini-2.0-flash` | Gemini API Key |
| DeepSeek | `deepseek-v4-flash` | DeepSeek API Key |

Натисни **Зберегти інтеграції**, потім **Перевірка інтеграцій** — має з'явитись 🟢.

Для кожного провайдера можна задати до 3 моделей (поля Model ID 1, 2, 3) і використовувати різні для моніторингу та дайджесту.

### 2.3 Вибір провайдера для задач

- Вкладка **«Моніторинг»** → поле «AI-провайдер» та «AI-модель»
- Вкладка **«Дайджест»** → аналогічно (можна вибрати інший провайдер/модель)

### 2.4 Джерела

Вкладка **«Джерела»**:
- Додай канал у форматі `@username` або `https://t.me/username`
- Переконайся, що «Моніторинг» увімкнений

Система автоматично почне збирати повідомлення та ставити їх у чергу AI-оцінки.

---

## 3. Перевірка після налаштування

```bash
# Стан моніторингу
curl http://127.0.0.1:8000/api/monitor/status

# Останні повідомлення
curl http://127.0.0.1:8000/api/messages?limit=5

# Стан Telethon сесії
curl -H "Authorization: Bearer $(sudo grep -oP '(?<=NEWSMON_API_TOKEN=)\S+' /etc/newsmon.env)" \
     http://127.0.0.1:8000/api/telethon/session/health
```

---

## 4. Оновлення

```bash
cd newsmon
sudo bash setup.sh --update
```

Виконує `git pull`, оновлює залежності та перезапускає сервіс.

---

## 5. Адмін-токен

Токен генерується автоматично при першому запуску `setup.sh` і зберігається в `/etc/newsmon.env`.

**Переглянути токен:**
```bash
sudo cat /etc/newsmon.env
```

**Змінити токен:**
```bash
NEW_TOKEN=$(openssl rand -hex 32)
echo "NEWSMON_API_TOKEN=$NEW_TOKEN" | sudo tee /etc/newsmon.env > /dev/null
sudo chmod 600 /etc/newsmon.env
sudo systemctl restart newsmon
echo "Новий токен: $NEW_TOKEN"
```

Після зміни всі браузери з старим токеном отримають 401 і запропонують ввести новий.

---

## 6. Корисні команди

```bash
# Логи в реальному часі
journalctl -u newsmon -f

# Статус сервісу
systemctl status newsmon

# Перезапуск
sudo systemctl restart newsmon

# Видалення сервісу (файли репо та БД НЕ видаляються)
sudo bash setup.sh --uninstall
```

---

## 7. Резервне копіювання

**Через UI:** Вкладка «Імпорт / Експорт» → «Завантажити резервну копію (.db)»

**Через термінал:**
```bash
cp /home/$USER/newsmon/backend/newsmon.db \
   /home/$USER/newsmon/backend/newsmon_$(date +%F_%H-%M-%S).db
```

**Відновлення з файлу:**
1. Через UI: вкладка «Імпорт / Експорт» → «Відновити з резервної копії»
2. Або вручну:
```bash
sudo systemctl stop newsmon
cp backup.db /home/$USER/newsmon/backend/newsmon.db
sudo systemctl start newsmon
```

---

## 8. Типові проблеми

### Сервіс не стартує — 503 «Сервер не налаштовано»
```bash
sudo cat /etc/newsmon.env          # перевір наявність токена
sudo systemctl daemon-reload
sudo systemctl restart newsmon
journalctl -u newsmon -n 50
```

### Telethon не авторизується (EOF / readonly)

```bash
# 1. Зупини сервіс
sudo systemctl stop newsmon

# 2. Видали старі session-файли
rm -f ~/newsmon/backend/telegram_user.session*

# 3. Виправ права
sudo chown -R $USER:$USER ~/newsmon

# 4. Запусти і авторизуйся знову через UI
sudo systemctl start newsmon
```

### Повідомлення не з'являються в Dashboard
- Перевір наявність активних джерел у вкладці «Джерела»
- Перевір стан Telethon: вкладка «Інтеграції» → блок Telethon
- Перевір логи: `journalctl -u newsmon -f`

### Не встановлюється пакет (pip install fails)
```bash
cd ~/newsmon/backend
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### DeepSeek повертає роздуми замість JSON

DeepSeek V4 є reasoning-моделлю — система автоматично відключає chain-of-thought для задач оцінки через параметр `thinking: disabled`. Якщо проблема зберігається, спробуй модель `deepseek-v4-flash` замість `deepseek-v4-pro`.

---

## 9. Ручне встановлення (без setup.sh)

Якщо потрібен повний контроль над процесом:

```bash
# Залежності
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip sqlite3

# Репозиторій
git clone https://github.com/MaanAndrii/newsmon.git
cd newsmon/backend

# Python venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Токен
ADMIN_TOKEN=$(openssl rand -hex 32)
sudo install -m 600 -o $USER -g $USER /dev/null /etc/newsmon.env
echo "NEWSMON_API_TOKEN=$ADMIN_TOKEN" | sudo tee /etc/newsmon.env
echo "Токен: $ADMIN_TOKEN"

# Systemd сервіс
sudo tee /etc/systemd/system/newsmon.service > /dev/null <<EOF
[Unit]
Description=NewsMon — Telegram News Monitor
After=network-online.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$(pwd)
EnvironmentFile=/etc/newsmon.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$(pwd)/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now newsmon
```
