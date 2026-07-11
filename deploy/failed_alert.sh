#!/usr/bin/env bash
# 텔레그램 발송 실패(status='failed')를 감시해 새 실패가 생겼을 때만 경보하는 cron 스크립트.
# crontab 예시(30분마다): */30 * * * * bash /path/to/kinkeeper/deploy/failed_alert.sh
#
# 배경: notifier가 3회 재시도 후에도 실패하면 해당 알림이 status='failed'로 남고 사유가
# error 컬럼에 기록되지만, 이를 아무도 모른다(docs/OPERATIONS.md §4 참조). 이 스크립트가
# 최근 24시간 내 failed 행을 조회해, '아직 경보하지 않은 새 실패(id > last_notified_id)'가
# 있을 때만 관리자 DM(ADMIN_CHAT_ID)으로 1건 경보한다.
#
# healthz_alert.sh와 같은 스타일: .env에서 자격증명 로드, 상태파일로 중복 경보 억제,
# 전송 실패는 삼켜 다음 회차에 재시도. pg_backup.sh처럼 compose로 db 컨테이너를 탐색한다.
set -euo pipefail

# .env에서 텔레그램 자격증명 + DB 정보 로드 (healthz_alert.sh/pg_backup.sh 스타일).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^(TELEGRAM_BOT_TOKEN|GROUP_CHAT_ID|ADMIN_CHAT_ID|POSTGRES_USER|POSTGRES_DB)=' "$ENV_FILE")
  set +a
fi

# 운영 경보는 가족방이 아니라 관리자 개인 DM으로 보낸다(ADMIN_CHAT_ID).
# 미설정 시 GROUP_CHAT_ID로 폴백해 경보 자체가 끊기지는 않게 한다.
ALERT_CHAT_ID="${ADMIN_CHAT_ID:-${GROUP_CHAT_ID:-}}"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "$ALERT_CHAT_ID" ]; then
  echo "❌ TELEGRAM_BOT_TOKEN/ADMIN_CHAT_ID(또는 GROUP_CHAT_ID)가 설정되지 않았습니다 ($ENV_FILE)" >&2
  exit 1
fi

DB_NAME="${POSTGRES_DB:-family_notifier}"
DB_USER="${POSTGRES_USER:-family}"

# 컨테이너 이름(<프로젝트명>-db-1)은 compose 프로젝트명에 좌우되므로 하드코딩하지 않고
# compose로 db 서비스 컨테이너를 탐색한다(pg_backup.sh와 동일).
CONTAINER="$(cd "$PROJECT_DIR" && docker compose ps -q db)"
if [ -z "$CONTAINER" ]; then
  echo "❌ db 컨테이너를 찾을 수 없습니다 (docker compose up -d db 필요)" >&2
  exit 1
fi

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/kinkeeper"
STATE_FILE="$STATE_DIR/failed_alert.state"
mkdir -p "$STATE_DIR"

# 이전에 경보한 마지막 failed id. 파일이 없으면 0(=아직 아무것도 경보 안 함).
LAST_NOTIFIED_ID=0
if [ -f "$STATE_FILE" ]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
fi

send_telegram() {
  # 전송 실패가 스크립트를 중단시키지 않도록 실패를 삼킨다(다음 회차에 재시도).
  # parse_mode 없이 평문 발송 — error 컬럼의 <, & 등을 그대로 실어도 안전(HTML escape 불필요).
  curl -sf -o /dev/null --max-time 10 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${ALERT_CHAT_ID}" \
    --data-urlencode "text=$1" \
    || echo "⚠️ 텔레그램 경보 전송 실패" >&2
}

HOST="$(hostname)"

# 최근 24시간 내 failed의 max(id)와 count. sent_at은 발송 시도 시각이라 실패 시각과 같다
# (bot/scheduler.py: 성공/실패 모두 sent_at=now()). -tA로 'MAX|COUNT' 한 줄을 받는다.
AGG="$(docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT COALESCE(MAX(id),0), COUNT(*) FROM scheduled_notifications WHERE status='failed' AND sent_at >= now() - interval '24 hours';")"
MAX_ID="${AGG%%|*}"
COUNT="${AGG##*|}"

# failed 0건이면 무발송 종료(cron 미등록 상태에서 수동 실행이 안전하도록).
if [ -z "$COUNT" ] || [ "$COUNT" -eq 0 ]; then
  exit 0
fi

# 이미 이 실패들을 경보했으면(새 id 없음) 조용히 종료. 새 실패가 없는 한 스팸하지 않는다.
if [ "$MAX_ID" -le "$LAST_NOTIFIED_ID" ]; then
  exit 0
fi

# 가장 최근 실패의 error 앞 80자(줄바꿈은 공백으로 눌러 한 줄로).
LATEST_ERROR="$(docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT left(error, 80) FROM scheduled_notifications WHERE status='failed' AND sent_at >= now() - interval '24 hours' ORDER BY sent_at DESC, id DESC LIMIT 1;" \
  | tr '\n\r' '  ')"

send_telegram "🔴 KinKeeper 발송 실패 ${COUNT}건 (최근 24h, 호스트: ${HOST})
최근 오류: ${LATEST_ERROR}
확인: docs/OPERATIONS.md §4 '텔레그램 발송 실패'"

# 이번에 경보한 최대 id를 기록해 같은 실패로 다시 경보하지 않도록 한다.
printf 'LAST_NOTIFIED_ID=%s\n' "$MAX_ID" > "$STATE_FILE"
