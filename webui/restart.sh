#!/usr/bin/env bash
set -u
PORT="${1:-8765}"
cd "$(dirname "$0")/.."

pkill -f "webui/server.py" 2>/dev/null
for _ in $(seq 1 20); do
  ss -ltn 2>/dev/null | grep -q ":${PORT}\b" || break
  sleep 0.3
done
if ss -ltn 2>/dev/null | grep -q ":${PORT}\b"; then
  echo "포트 ${PORT} 를 아직 누가 쓰고 있습니다:"
  ss -ltnp 2>/dev/null | grep ":${PORT}\b"
  exit 1
fi

setsid nohup python3 webui/server.py --port "${PORT}" > /tmp/sss-webui.log 2>&1 < /dev/null &
sleep 2
if curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/" | grep -q 200; then
  echo "웹 UI 준비됨 -> http://127.0.0.1:${PORT}   (로그: /tmp/sss-webui.log)"
else
  echo "기동 실패. 로그:"; tail -20 /tmp/sss-webui.log; exit 1
fi
