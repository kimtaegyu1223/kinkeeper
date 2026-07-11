#!/usr/bin/env bash
# /healthz 실패 시 텔레그램으로 경보를 보내는 cron 스크립트.
# crontab 예시(5분마다): */5 * * * * bash /path/to/kinkeeper/deploy/healthz_alert.sh
#
# 결정: cron 경보(2026-07-11). 외부 uptime 모니터 대신 이 스크립트를 붙인다.
# 연속 장애 스팸 방지를 위해 상태파일 기반 쿨다운(같은 장애당 1시간 1회)을 두고,
# 정상으로 돌아오면 '복구됨' 1회를 보낸다. pg_backup.sh와 같은 방식으로 .env를 읽는다.
set -euo pipefail

HEALTHZ_URL="http://127.0.0.1:8000/healthz"
COOLDOWN=3600  # 같은 장애당 재경보 최소 간격(초) = 1시간

# .env에서 텔레그램 자격증명 로드 (pg_backup.sh 스타일 + set -a로 export).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^(TELEGRAM_BOT_TOKEN|GROUP_CHAT_ID)=' "$ENV_FILE")
  set +a
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${GROUP_CHAT_ID:-}" ]; then
  echo "❌ TELEGRAM_BOT_TOKEN/GROUP_CHAT_ID가 설정되지 않았습니다 ($ENV_FILE)" >&2
  exit 1
fi

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/kinkeeper"
STATE_FILE="$STATE_DIR/healthz_alert.state"
mkdir -p "$STATE_DIR"

# 이전 상태 로드 — 파일이 없으면 정상(up)으로 간주.
PREV_STATUS="up"
LAST_ALERT=0
if [ -f "$STATE_FILE" ]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
fi

write_state() {
  # $1=status(up|down) $2=last_alert_epoch
  printf 'PREV_STATUS=%s\nLAST_ALERT=%s\n' "$1" "$2" > "$STATE_FILE"
}

send_telegram() {
  # 전송 실패가 스크립트를 중단시키지 않도록 실패를 삼킨다(다음 회차에 재시도).
  curl -sf -o /dev/null --max-time 10 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${GROUP_CHAT_ID}" \
    --data-urlencode "text=$1" \
    || echo "⚠️ 텔레그램 경보 전송 실패" >&2
}

HOST="$(hostname)"
NOW="$(date +%s)"

# curl -sf: 연결 실패(웹 다운)와 503(DB 장애) 모두 비정상으로 취급.
if curl -sf -o /dev/null --max-time 10 "$HEALTHZ_URL"; then
  if [ "$PREV_STATUS" = "down" ]; then
    send_telegram "🟢 KinKeeper /healthz 복구됨 (호스트: ${HOST})"
  fi
  write_state up 0
else
  if [ "$PREV_STATUS" != "down" ] || [ $((NOW - LAST_ALERT)) -ge "$COOLDOWN" ]; then
    send_telegram "🔴 KinKeeper /healthz 실패 (호스트: ${HOST}) — 웹/DB 상태를 확인하세요."
    write_state down "$NOW"
  else
    # 아직 장애 중이고 쿨다운 이내 — 재경보 없이 기존 경보 시각 유지.
    write_state down "$LAST_ALERT"
  fi
fi
