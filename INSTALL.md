# NewsMon — інструкція встановлення на Raspberry Pi

> Оновлено: листопад 2026
>
> Ціль: підняти робочу систему (FastAPI + SQLite + Telethon + Web UI) на Raspberry Pi в локальній мережі.

---

## Вимоги

### Апаратні
- Raspberry Pi 4 / 5 (рекомендовано 4+ GB RAM)
- SSD через USB 3.0 (бажано) або microSD
- Блок живлення USB-C 15 Вт для Pi 4 або 27 Вт для Pi 5, особливо з USB SSD. [Рекомендації Raspberry Pi](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)
- Доступ до інтернету

### Програмні
- Raspberry Pi OS Lite 64-bit. Для нового встановлення обери Trixie, наявний Bookworm також має потрібний Python. [Документація Raspberry Pi OS](https://www.raspberrypi.com/documentation/computers/os.html)
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

Скрипт автоматично виконає всі кроки нижче: встановить залежності, створить Python venv, налаштує та запустить systemd-сервіс.

Після завершення в терміналі буде адреса сервера (`http://<IP>:8000`).

---

## 2. Перша конфігурація в браузері

Відкрий `http://<IP Raspberry Pi>:8000/settings.html`

При першому відкритті з'явиться вікно авторизації — введи пароль `admin` (за замовчуванням). Змінити пароль можна у вкладці **«Імпорт / Експорт»** → «Пароль адмінки».

### 2.1 Telegram API + Telethon авторизація

Вкладка **«API та інтеграції»**:

1. Введи `Telegram API ID` та `Telegram API Hash`
2. Натисни **Зберегти інтеграції**
3. У блоці Telethon:
   - Натисни **Підключити акаунт через QR**.
   - На смартфоні відкрий **Telegram → Налаштування → Пристрої → Підключити пристрій** і відскануй код.
   - Якщо Telegram запросить пароль двоетапної перевірки, введи його в адмінці.
   - Дочекайся повідомлення **Акаунт Telegram підключено**. Цей акаунт стане активним для моніторингу.

### 2.2 AI-провайдер

У тій само вкладці обери та налаштуй один провайдер:

Введи ключ і Model ID з кабінету свого провайдера. Натисни **Зберегти інтеграції**, потім **Перевірити підключення**. Для AI-провайдерів ця кнопка перевіряє заповнення та формат полів. Реальний запит до моделі відбудеться під час AI-оцінки або генерації дайджесту.

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

# Стан Telethon сесії (замінити MY_PASSWORD на свій пароль)
curl -H "Authorization: Bearer MY_PASSWORD" \
     http://127.0.0.1:8000/api/telethon/session/health
```

---

## 4. Оновлення

```bash
cd newsmon
sudo bash setup.sh --update
```

Скрипт оновлює поточну гілку з її upstream лише через fast-forward, встановлює залежності, перезапускає сервіс і чекає відповіді HTTP. Локальні файли й правки, які Git може зберегти під час оновлення, залишаються на місці. Якщо оновлення зачіпає локальні правки або гілки розійшлися, Git зупиниться й виведе причину в журнал. Перед оновленням зроби резервну копію. Systemd запускає сам NewsMon після завантаження Raspberry Pi.

Також можна відкрити **Імпорт / Експорт → Оновлення NewsMon → Оновити систему**. Для наявної інсталяції спочатку отримай версію з цією функцією через термінал і один раз запусти `sudo bash setup.sh` із тієї самої папки. Скрипт встановить окремий systemd-сервіс оновлення. Для приватного репозиторію Git має мати доступ без інтерактивного запиту від імені користувача інсталяції.

Якщо оновлення зупиниться після завантаження коду, скрипт виведе помилку. Для запусків з адмінки переглянь `sudo journalctl -u newsmon-update -n 50`, усунь причину та повтори команду. Автоматичного відкату коду та залежностей немає.

Якщо системний Python змінився або `.venv` пошкоджено, зупини сервіс і створи середовище заново:

```bash
sudo systemctl stop newsmon
mv backend/.venv "backend/.venv.old-$(date +%F_%H-%M-%S)"
sudo bash setup.sh
```

---

## 5. Пароль адміністратора

Пароль за замовчуванням: `admin`. Зберігається в SQLite — не потребує файлів конфігурації чи env-змінних.

**Змінити пароль через UI:**
- Відкрий вкладку **«Імпорт / Експорт»**
- Знайди картку «Пароль адмінки»
- Введи новий пароль (мінімум 4 символи) і натисни «Зберегти пароль»

Після зміни браузер автоматично запропонує ввести новий пароль.

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

**Через термінал** у корені репозиторію:
```bash
test -f backend/newsmon.db &&
  (umask 077; sqlite3 backend/newsmon.db ".backup 'newsmon_$(date +%F_%H-%M-%S).db'")
```

Резервна копія містить пароль адмінки, API-ключі та збережену в SQLite сесію Telethon. Для старої файлової сесії окремо збережи `backend/telegram_user.session` після зупинки сервісу.

**Відновлення з файлу:**
1. Через UI: вкладка «Імпорт / Експорт» → «Відновити з резервної копії». Потім перезапусти сервіс.
2. Або вручну з кореня репозиторію, від імені власника інсталяції:
```bash
sudo systemctl stop newsmon
sqlite3 -bail backend/newsmon.db ".restore 'backup.db'" &&
  sudo systemctl start newsmon
```

---

## 8. Типові проблеми

### Сервіс не стартує
```bash
sudo systemctl daemon-reload
sudo systemctl restart newsmon
journalctl -u newsmon -n 50
```

### Telethon не авторизується (EOF / readonly)

```bash
# 1. Зупини сервіс
sudo systemctl stop newsmon

# 2. Віднови права власника за потреби, з кореня репозиторію
sudo chown -R "$(id -un):$(id -gn)" backend

# 3. Запусти сервіс, очисть стару сесію через «Вийти з Telethon»
# і підключи акаунт через QR
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

# Systemd сервіс
sudo tee /etc/systemd/system/newsmon.service > /dev/null <<EOF
[Unit]
Description=NewsMon — Telegram News Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment=PYTHONUNBUFFERED=1
ExecStart="$(pwd)/.venv/bin/python" -m uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now newsmon
```
