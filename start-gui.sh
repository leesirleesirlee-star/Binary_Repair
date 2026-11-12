#!/bin/sh
# BinaryRepair 新 GUI 启动脚本（入口 = 浏览器操作台）
# 用法：./start-gui.sh  然后打开它打印的带 token 的地址
set -eu
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
PORT="${BINARYREPAIR_PORT:-8123}"
HOST="${BINARYREPAIR_HOST:-127.0.0.1}"
VENV="${VENV:-.venv}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "[start-gui] creating $VENV ..."
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check -r requirements.txt
fi

mkdir -p var
if [ ! -s var/session.token ]; then
  "$VENV/bin/python" -c "import secrets;print(secrets.token_urlsafe(32))" > var/session.token
fi
TOKEN=$(cat var/session.token)
export BINARYREPAIR_SESSION_TOKEN="$TOKEN"

echo ""
echo "  打开: http://127.0.0.1:${PORT}/?token=${TOKEN}"
echo ""
exec "$VENV/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
