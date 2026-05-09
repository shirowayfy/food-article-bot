#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/shirowayfy/food-article-bot.git"
INSTALL_DIR="/opt/food-diary-bot"
SERVICE_NAME="food-diary-bot"

if ! command -v apt-get >/dev/null 2>&1; then
    echo "Скрипт рассчитан на Debian/Ubuntu (apt-get не найден)." >&2
    exit 1
fi

# Detect privilege escalation tool. Order: sudo > usudo > doas > already-root.
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    echo "Внимание: запуск от root — сервис будет работать от root."
    echo "Лучше выйти и запустить от обычного юзера (Ctrl+C для отмены)."
    sleep 3
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
elif command -v usudo >/dev/null 2>&1; then
    SUDO="usudo"
elif command -v doas >/dev/null 2>&1; then
    SUDO="doas"
else
    echo "Не нашёл sudo/usudo/doas. Запусти от root или установи sudo." >&2
    exit 1
fi

if [ -n "$SUDO" ]; then
    echo "==> Проверяю $SUDO (может попросить пароль)..."
    $SUDO true
fi

read -rsp "BOT_TOKEN (от @BotFather): " BOT_TOKEN; echo
[ -n "$BOT_TOKEN" ] || { echo "BOT_TOKEN пустой, выход" >&2; exit 1; }

read -rsp "IMGBB_API_KEY (api.imgbb.com): " IMGBB_API_KEY; echo
[ -n "$IMGBB_API_KEY" ] || { echo "IMGBB_API_KEY пустой, выход" >&2; exit 1; }

read -rp "TELEGRAPH_AUTHOR_NAME [Food Diary]: " TELEGRAPH_AUTHOR_NAME
TELEGRAPH_AUTHOR_NAME=${TELEGRAPH_AUTHOR_NAME:-Food Diary}

echo "==> Устанавливаю системные пакеты..."
$SUDO apt-get update
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git

echo "==> Получаю код из $REPO_URL..."
if [ -d "$INSTALL_DIR/.git" ]; then
    $SUDO chown -R "$USER:$USER" "$INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
else
    $SUDO mkdir -p "$INSTALL_DIR"
    $SUDO chown "$USER:$USER" "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "==> Готовлю виртуальное окружение..."
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "==> Записываю .env..."
cat > "$INSTALL_DIR/.env" <<EOF
BOT_TOKEN=$BOT_TOKEN
IMGBB_API_KEY=$IMGBB_API_KEY
TELEGRAPH_AUTHOR_NAME=$TELEGRAPH_AUTHOR_NAME
TELEGRAPH_AUTHOR_URL=
DB_PATH=$INSTALL_DIR/food_diary.db
EOF
chmod 600 "$INSTALL_DIR/.env"

echo "==> Создаю systemd unit /etc/systemd/system/${SERVICE_NAME}.service..."
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=Food Diary Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Запускаю сервис..."
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME"
$SUDO systemctl restart "$SERVICE_NAME"

sleep 2
$SUDO systemctl --no-pager status "$SERVICE_NAME" || true

SUDO_HINT=${SUDO:-}
echo
echo "Готово."
echo "  Логи:       journalctl -u $SERVICE_NAME -f"
echo "  Рестарт:    $SUDO_HINT systemctl restart $SERVICE_NAME"
echo "  Обновление: cd $INSTALL_DIR && git pull && $SUDO_HINT systemctl restart $SERVICE_NAME"
