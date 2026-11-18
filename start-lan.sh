#!/bin/sh
# Stable same-network launcher (no ngrok, no public tunnel).
# URL: http://<bonjour-hostname>.local:8124/?token=...
set -eu
cd "$(dirname "$0")"

PORT="${BINARYREPAIR_LAN_PORT:-8124}"
TOKEN_FILE="${BINARYREPAIR_LAN_TOKEN_FILE:-var/lan.token}"
DATA_DIR="${BINARYREPAIR_LAN_DATA_DIR:-var/lan-data}"
VENV="${VENV:-.venv}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "[lan] missing $VENV/bin/python; run ./start-gui.sh once first" >&2
  exit 1
fi

mkdir -p var "$DATA_DIR"
if [ ! -s "$TOKEN_FILE" ]; then
  "$VENV/bin/python" -c "import secrets;print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
TOKEN=$(cat "$TOKEN_FILE")
export BINARYREPAIR_SESSION_TOKEN="$TOKEN"

LOCAL_HOST=$(scutil --get LocalHostName 2>/dev/null || hostname -s)
MDNS_HOST="${LOCAL_HOST}.local"
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
if [ -z "${BINARYREPAIR_CGC_ROOT:-}" ] && [ -d "$HOME/cb-multios/build64/challenges" ]; then
  export BINARYREPAIR_CGC_ROOT="$HOME/cb-multios/build64/challenges"
fi
HOSTS="127.0.0.1,localhost,${MDNS_HOST}"
[ -n "$IP" ] && HOSTS="$HOSTS,$IP"
export BINARYREPAIR_ALLOWED_HOSTS="$HOSTS"

echo ""
echo "  BinaryRepair · 同一网络固定入口"
echo "  主机名: http://${MDNS_HOST}:${PORT}/?token=${TOKEN}"
[ -n "$IP" ] && echo "  局域网 IP: http://${IP}:${PORT}/?token=${TOKEN}"
echo "  Local:  http://127.0.0.1:${PORT}/welcome?token=${TOKEN}"
echo ""
echo "  token 文件: ${TOKEN_FILE} (0600)"
echo "  数据目录:   ${DATA_DIR}"
echo ""

exec "$VENV/bin/python" -m uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" \
  --app-dir "$(pwd)"
