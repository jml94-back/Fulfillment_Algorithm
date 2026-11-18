#!/usr/bin/env bash
# Mac mini M4 에서 브로커 준비·실행.
#   ./setup_mac.sh            비밀번호 만들고 포그라운드 실행
#   ./setup_mac.sh --service  brew services 로 상시 실행 (재부팅 후 자동 시작)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
command -v mosquitto >/dev/null || brew install mosquitto
MOSQ="$(command -v mosquitto || echo /opt/homebrew/sbin/mosquitto)"
PASSWD_BIN="$(command -v mosquitto_passwd || echo /opt/homebrew/bin/mosquitto_passwd)"

mkdir -p "$DIR/data"
if [ ! -f "$DIR/passwd" ]; then
  echo "역할별 비밀번호를 정합니다 (unity scheduler rl control sensor monitor admin)."
  : > "$DIR/passwd"
  for u in unity scheduler rl control sensor monitor admin; do
    "$PASSWD_BIN" -b "$DIR/passwd" "$u" "${WH_MQTT_PASSWORD:-$(openssl rand -hex 8)}"
  done
  chmod 600 "$DIR/passwd"
  echo "passwd 파일 생성. 비밀번호를 정해 쓰려면: $PASSWD_BIN $DIR/passwd <역할>"
fi
sed "s#__DIR__#$DIR#g" "$DIR/mosquitto.conf" > "$DIR/mosquitto.local.conf"

if [ "${1:-}" = "--service" ]; then
  BREW_CONF="$(brew --prefix)/etc/mosquitto/mosquitto.conf"
  cp "$DIR/mosquitto.local.conf" "$BREW_CONF"
  brew services restart mosquitto
  echo "brew services 로 실행 중. 설정: $BREW_CONF"
else
  echo "포트 1883(MQTT), 9001(WebSocket). 중지: Ctrl+C"
  exec "$MOSQ" -c "$DIR/mosquitto.local.conf"
fi
