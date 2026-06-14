#!/usr/bin/env bash
# setup.sh — NewsMon installer / updater for Raspberry Pi (Debian/Ubuntu)
# Usage:
#   First install : bash setup.sh
#   Update        : bash setup.sh --update
#   Uninstall     : bash setup.sh --uninstall
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}━━━ $* ━━━${RESET}"; }
die()     { error "$*"; exit 1; }

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
BACKEND_DIR="$REPO_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
SERVICE_NAME="newsmon"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/newsmon.env"
PORT=8000
CURRENT_USER="${SUDO_USER:-$USER}"
CURRENT_HOME=$(eval echo "~$CURRENT_USER")

MODE="install"
[[ "${1:-}" == "--update" ]]    && MODE="update"
[[ "${1:-}" == "--uninstall" ]] && MODE="uninstall"

# ── Guards ────────────────────────────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] || die "Запусти з sudo: sudo bash setup.sh"
[[ "$CURRENT_USER" == "root" ]] && die "Не запускай від імені root (використай sudo з звичайного акаунта)"
[[ -f "$BACKEND_DIR/app.py" ]]  || die "Не знайдено backend/app.py. Запусти скрипт з кореня репозиторію newsmon."

# ── Helpers ───────────────────────────────────────────────────────────────────
get_local_ip() {
    hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1"
}

python_ok() {
    local py="$1"
    command -v "$py" &>/dev/null || return 1
    local ver
    ver=$("$py" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
    [[ "$ver" == "True" ]]
}

find_python() {
    for py in python3.13 python3.12 python3.11 python3; do
        python_ok "$py" && echo "$py" && return 0
    done
    return 1
}

run_as_user() {
    sudo -u "$CURRENT_USER" "$@"
}

# ─────────────────────────────────────────────────────────────────────────────
# UNINSTALL
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "uninstall" ]]; then
    header "Видалення NewsMon"
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        info "Зупиняємо сервіс..."; systemctl stop "$SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
    fi
    [[ -f "$SERVICE_FILE" ]] && rm -f "$SERVICE_FILE" && success "Сервіс видалено"
    systemctl daemon-reload
    if [[ -f "$ENV_FILE" ]]; then
        read -rp "Видалити $ENV_FILE (містить токен)? [y/N] " ans
        [[ "$ans" =~ ^[Yy]$ ]] && rm -f "$ENV_FILE" && success "Env-файл видалено"
    fi
    if [[ -d "$VENV_DIR" ]]; then
        read -rp "Видалити Python venv ($VENV_DIR)? [y/N] " ans
        [[ "$ans" =~ ^[Yy]$ ]] && rm -rf "$VENV_DIR" && success "venv видалено"
    fi
    success "Готово. Файли репозиторію та БД НЕ видалено."
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "update" ]]; then
    header "Оновлення NewsMon"
    info "Оновлюємо код..."
    run_as_user git -C "$REPO_DIR" pull --rebase
    success "Код оновлено"

    info "Оновлюємо залежності..."
    run_as_user "$VENV_DIR/bin/pip" install -q --upgrade pip
    run_as_user "$VENV_DIR/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
    success "Залежності оновлено"

    info "Перезапускаємо сервіс..."
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Сервіс запущено"
    else
        error "Сервіс не запустився. Перевір: journalctl -u $SERVICE_NAME -n 50"
        exit 1
    fi

    IP=$(get_local_ip)
    echo -e "\n${GREEN}${BOLD}NewsMon оновлено!${RESET}"
    echo -e "  Dashboard : ${CYAN}http://$IP:$PORT/dashboard.html${RESET}"
    echo -e "  Логи      : ${CYAN}journalctl -u $SERVICE_NAME -f${RESET}"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL
# ─────────────────────────────────────────────────────────────────────────────

echo -e "\n${BOLD}${CYAN}"
echo "  ███╗   ██╗███████╗██╗    ██╗███████╗███╗   ███╗ ██████╗ ███╗   ██╗"
echo "  ████╗  ██║██╔════╝██║    ██║██╔════╝████╗ ████║██╔═══██╗████╗  ██║"
echo "  ██╔██╗ ██║█████╗  ██║ █╗ ██║███████╗██╔████╔██║██║   ██║██╔██╗ ██║"
echo "  ██║╚██╗██║██╔══╝  ██║███╗██║╚════██║██║╚██╔╝██║██║   ██║██║╚██╗██║"
echo "  ██║ ╚████║███████╗╚███╔███╔╝███████║██║ ╚═╝ ██║╚██████╔╝██║ ╚████║"
echo "  ╚═╝  ╚═══╝╚══════╝ ╚══╝╚══╝ ╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝"
echo -e "${RESET}"
echo -e "  Встановлення на ${BOLD}$(hostname)${RESET} від імені ${BOLD}$CURRENT_USER${RESET}"
echo ""

# ── Step 1: System packages ───────────────────────────────────────────────────
header "1. Системні пакети"
apt-get update -qq
PKGS_NEEDED=()
for pkg in git python3 python3-venv python3-pip sqlite3 curl; do
    dpkg -s "$pkg" &>/dev/null || PKGS_NEEDED+=("$pkg")
done
if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
    info "Встановлюємо: ${PKGS_NEEDED[*]}"
    apt-get install -y -qq "${PKGS_NEEDED[@]}"
fi
success "Системні пакети готові"

# ── Step 2: Python version ────────────────────────────────────────────────────
header "2. Python"
PYTHON=$(find_python) || die "Потрібен Python 3.11+. Встанови: sudo apt install python3.11"
PY_VER=$("$PYTHON" --version 2>&1)
success "$PY_VER знайдено ($PYTHON)"

# ── Step 3: Python venv ───────────────────────────────────────────────────────
header "3. Python venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Створюємо venv..."
    run_as_user "$PYTHON" -m venv "$VENV_DIR"
    success "venv створено: $VENV_DIR"
else
    success "venv вже існує"
fi

info "Оновлюємо pip та встановлюємо залежності..."
run_as_user "$VENV_DIR/bin/pip" install -q --upgrade pip
run_as_user "$VENV_DIR/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
success "Залежності встановлено"

# ── Step 4: Admin token ───────────────────────────────────────────────────────
header "4. Адмін-токен"
EXISTING_TOKEN=""
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_TOKEN=$(grep -oP '(?<=NEWSMON_API_TOKEN=)\S+' "$ENV_FILE" 2>/dev/null || true)
fi

DEFAULT_TOKEN="change-me-now"

if [[ -n "$EXISTING_TOKEN" ]]; then
    success "Токен вже існує в $ENV_FILE"
    ADMIN_TOKEN="$EXISTING_TOKEN"
else
    ADMIN_TOKEN="$DEFAULT_TOKEN"
    install -m 600 -o "$CURRENT_USER" -g "$CURRENT_USER" /dev/null "$ENV_FILE"
    echo "NEWSMON_API_TOKEN=$ADMIN_TOKEN" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    chown "$CURRENT_USER:$CURRENT_USER" "$ENV_FILE"
    success "Дефолтний пароль записано в $ENV_FILE"
fi

# ── Step 5: systemd service ───────────────────────────────────────────────────
header "5. Systemd сервіс"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=NewsMon — Telegram News Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$BACKEND_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/uvicorn app:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
success "Сервіс записано: $SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" --quiet
success "Автозапуск увімкнено"

# ── Step 6: Start service ─────────────────────────────────────────────────────
header "6. Запуск сервісу"
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Перезапускаємо (вже запущений)..."
    systemctl restart "$SERVICE_NAME"
else
    systemctl start "$SERVICE_NAME"
fi

info "Чекаємо старту..."
for i in {1..10}; do
    sleep 1
    if curl -sf "http://127.0.0.1:$PORT/api/monitor/status" -o /dev/null 2>/dev/null; then
        break
    fi
    [[ $i -eq 10 ]] && {
        error "Сервіс не відповідає після 10 секунд."
        error "Перевір логи: journalctl -u $SERVICE_NAME -n 50"
        exit 1
    }
done
success "Сервіс запущено і відповідає"

# ── Step 7: Summary ───────────────────────────────────────────────────────────
IP=$(get_local_ip)

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║           NewsMon успішно встановлено!                  ║${RESET}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Адреси:${RESET}"
echo -e "    Dashboard    →  ${CYAN}http://$IP:$PORT/dashboard.html${RESET}"
echo -e "    Налаштування →  ${CYAN}http://$IP:$PORT/settings.html${RESET}"
echo ""
if [[ "$ADMIN_TOKEN" == "$DEFAULT_TOKEN" ]]; then
echo -e "  ${BOLD}Пароль для першого входу:${RESET}"
echo -e "    ${YELLOW}${BOLD}$ADMIN_TOKEN${RESET}"
echo ""
echo -e "  ${RED}${BOLD}⚠  ЗМІНЬ ПАРОЛЬ ОДРАЗУ ПІСЛЯ ПЕРШОГО ВХОДУ!${RESET}"
echo -e "  ${RED}Налаштування → Імпорт/Експорт → Пароль адмінки${RESET}"
else
echo -e "  ${BOLD}Пароль:${RESET} існуючий (з $ENV_FILE)"
fi
echo ""
echo -e "  ${BOLD}Наступні кроки в браузері:${RESET}"
echo -e "    1. Відкрий ${CYAN}http://$IP:$PORT/settings.html${RESET}"
echo -e "    2. Введи пароль ${YELLOW}${BOLD}${ADMIN_TOKEN}${RESET} у вікні авторизації"
echo -e "    3. ${RED}Змінь пароль${RESET}: Імпорт/Експорт → Пароль адмінки"
echo -e "    4. Вкладка «Інтеграції» → додай Telegram API ID + Hash"
echo -e "    5. Авторизуй Telethon (телефон → код → 2FA)"
echo -e "    6. Додай API ключ AI (Claude / Grok / Gemini / DeepSeek)"
echo -e "    7. Вкладка «Джерела» → додай Telegram-канали"
echo ""
echo -e "  ${BOLD}Корисні команди:${RESET}"
echo -e "    Логи      →  ${CYAN}journalctl -u $SERVICE_NAME -f${RESET}"
echo -e "    Статус    →  ${CYAN}systemctl status $SERVICE_NAME${RESET}"
echo -e "    Оновлення →  ${CYAN}sudo bash $SCRIPT_DIR/setup.sh --update${RESET}"
echo -e "    Токен     →  ${CYAN}sudo cat $ENV_FILE${RESET}"
echo ""
