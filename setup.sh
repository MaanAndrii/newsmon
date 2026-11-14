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
UPDATE_UNIT="/etc/systemd/system/newsmon-update.service"
UPDATE_SUDOERS="/etc/sudoers.d/newsmon-update"
PORT=8000
CURRENT_USER="${SUDO_USER:-$(id -un)}"

[[ $# -le 1 ]] || die "Використання: sudo bash setup.sh [--update | --uninstall]"
case "${1:-}" in
    "") MODE="install" ;;
    --update) MODE="update" ;;
    --uninstall) MODE="uninstall" ;;
    -h|--help) echo "Використання: sudo bash setup.sh [--update | --uninstall]"; exit 0 ;;
    *) die "Невідомий параметр: $1" ;;
esac

# ── Guards ────────────────────────────────────────────────────────────────────
[[ "$EUID" -eq 0 || "$MODE" == "update" ]] || die "Запусти з sudo: sudo bash setup.sh"
[[ "$CURRENT_USER" == "root" ]] && die "Не запускай від імені root (використай sudo з звичайного акаунта)"
[[ -f "$BACKEND_DIR/app.py" ]]  || die "Не знайдено backend/app.py. Запусти скрипт з кореня репозиторію newsmon."

# ── Helpers ───────────────────────────────────────────────────────────────────
get_local_ip() {
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i ~ /^[0-9.]+$/) {print $i; exit}}') || true
    echo "${ip:-127.0.0.1}"
}

python_ok() {
    local py="$1"
    command -v "$py" &>/dev/null || return 1
    local ver
    ver=$("$py" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
    [[ "$ver" == "True" ]]
}

run_as_user() {
    if [[ "$EUID" -eq 0 ]]; then
        sudo -H -u "$CURRENT_USER" "$@"
    else
        "$@"
    fi
}

install_dependencies() {
    run_as_user "$VENV_DIR/bin/python" -m pip install -q -r "$BACKEND_DIR/requirements.txt"
    run_as_user "$VENV_DIR/bin/python" -m pip check
}

wait_for_service() {
    local attempt
    for attempt in {1..30}; do
        if systemctl is-active --quiet "$SERVICE_NAME" &&
           curl --fail --silent --max-time 2 "http://127.0.0.1:$PORT/api/monitor/status" -o /dev/null; then
            return
        fi
        sleep 1
    done
    die "Сервіс не відповідає. Перевір: sudo journalctl -u $SERVICE_NAME -n 50"
}

# ─────────────────────────────────────────────────────────────────────────────
# UNINSTALL
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "uninstall" ]]; then
    header "Видалення NewsMon"
    if [[ -f "$UPDATE_UNIT" ]]; then
        systemctl stop newsmon-update.service
        rm -f -- "$UPDATE_UNIT" "$UPDATE_SUDOERS"
    fi
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        info "Зупиняємо сервіс..."; systemctl stop "$SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
    fi
    [[ -f "$SERVICE_FILE" ]] && rm -f "$SERVICE_FILE" && success "Сервіс видалено"
    systemctl daemon-reload
    if [[ -d "$VENV_DIR" ]]; then
        if read -rp "Видалити Python venv ($VENV_DIR)? [y/N] " ans && [[ "$ans" =~ ^[Yy]$ ]]; then
            run_as_user rm -rf -- "$VENV_DIR"
            success "venv видалено"
        fi
    fi
    success "Готово. Файли репозиторію та БД НЕ видалено."
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "update" ]]; then
    header "Оновлення NewsMon"
    [[ -f "$SERVICE_FILE" ]] || die "Спочатку встанови NewsMon: sudo bash setup.sh"
    exec 9<"$BACKEND_DIR"
    flock -n 9 || die "Оновлення NewsMon уже виконується."
    python_ok "$VENV_DIR/bin/python" || die "Python у venv відсутній або застарілий. Віднови venv за INSTALL.md."
    git_status=$(run_as_user git -C "$REPO_DIR" status --porcelain) ||
        die "Не вдалося перевірити стан репозиторію."
    [[ -z "$git_status" ]] ||
        die "У репозиторії є локальні зміни. Збережи їх перед оновленням."
    info "Оновлюємо код..."
    run_as_user git -C "$REPO_DIR" pull --ff-only
    success "Код оновлено"

    info "Оновлюємо залежності..."
    install_dependencies
    success "Залежності оновлено"

    info "Перезапускаємо сервіс..."
    if [[ "$EUID" -eq 0 ]]; then
        systemctl restart newsmon.service
    else
        sudo -n /usr/bin/systemctl restart newsmon.service
    fi
    wait_for_service

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
for pkg in git python3 python3-venv python3-pip sqlite3 curl util-linux; do
    [[ "$(dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null)" == "installed" ]] ||
        PKGS_NEEDED+=("$pkg")
done
if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
    info "Встановлюємо: ${PKGS_NEEDED[*]}"
    apt-get install -y -qq "${PKGS_NEEDED[@]}"
fi
success "Системні пакети готові"

# ── Step 2: Python version ────────────────────────────────────────────────────
header "2. Python"
PYTHON=python3
python_ok "$PYTHON" || die "Потрібен системний Python 3.11+ із python3-venv."
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

python_ok "$VENV_DIR/bin/python" || die "Віднови venv за INSTALL.md."
info "Встановлюємо залежності..."
install_dependencies
success "Залежності встановлено"

# ── Step 4: systemd service ───────────────────────────────────────────────────
header "4. Systemd сервіс"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=NewsMon — Telegram News Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$BACKEND_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart="$VENV_DIR/bin/python" -m uvicorn app:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
success "Сервіс записано: $SERVICE_FILE"

cat > "$UPDATE_UNIT" <<EOF
[Unit]
Description=Update NewsMon
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=$CURRENT_USER
WorkingDirectory=$REPO_DIR
Environment=GIT_TERMINAL_PROMPT=0
ExecStart=/bin/bash ./setup.sh --update
TimeoutStartSec=20min
EOF

[[ "$CURRENT_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || die "Ім'я користувача несумісне з налаштуванням sudoers."
sudoers_tmp=$(mktemp)
trap 'rm -f -- "$sudoers_tmp"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl restart --no-block newsmon-update.service, /usr/bin/systemctl restart newsmon.service\n' "$CURRENT_USER" > "$sudoers_tmp"
/usr/sbin/visudo -cf "$sudoers_tmp"
install -o root -g root -m 0440 "$sudoers_tmp" "$UPDATE_SUDOERS"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" --quiet
success "Автозапуск увімкнено"

# ── Step 5: Start service ─────────────────────────────────────────────────────
header "5. Запуск сервісу"
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Перезапускаємо (вже запущений)..."
    systemctl restart "$SERVICE_NAME"
else
    systemctl start "$SERVICE_NAME"
fi

info "Чекаємо старту..."
wait_for_service
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
echo -e "  ${BOLD}Пароль для першого входу:${RESET}  ${YELLOW}${BOLD}admin${RESET}"
echo -e "  ${RED}${BOLD}⚠  Змінь пароль після першого входу!${RESET}"
echo -e "  ${CYAN}Налаштування → Імпорт/Експорт → Пароль адмінки${RESET}"
echo ""
echo -e "  ${BOLD}Наступні кроки в браузері:${RESET}"
echo -e "    1. Відкрий ${CYAN}http://$IP:$PORT/settings.html${RESET}"
echo -e "    2. Введи пароль ${YELLOW}${BOLD}admin${RESET}"
echo -e "    3. ${RED}Змінь пароль${RESET}: Імпорт/Експорт → Пароль адмінки"
echo -e "    4. Вкладка «Інтеграції» → додай Telegram API ID + Hash"
echo -e "    5. Підключи акаунт через QR у блоці Telethon"
echo -e "    6. Додай API ключ AI (Claude / Grok / Gemini / DeepSeek)"
echo -e "    7. Вкладка «Джерела» → додай Telegram-канали"
echo ""
echo -e "  ${BOLD}Корисні команди:${RESET}"
echo -e "    Логи      →  ${CYAN}journalctl -u $SERVICE_NAME -f${RESET}"
echo -e "    Статус    →  ${CYAN}systemctl status $SERVICE_NAME${RESET}"
echo -e "    Оновлення →  ${CYAN}sudo bash \"$SCRIPT_DIR/setup.sh\" --update${RESET}"
echo ""
