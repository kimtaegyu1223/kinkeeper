#!/usr/bin/env bash
# PostgreSQL 일일 백업 — crontab에 등록해서 사용
# crontab 예시: 0 2 * * * bash /path/to/kinkeeper/deploy/pg_backup.sh
set -euo pipefail
umask 077  # 백업 덤프 파일이 생성 시점부터 소유자 전용(600)이 되도록 강제

# .env에서 DB 정보 로드
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$PROJECT_DIR/.env" ]; then
  source <(grep -E '^(POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD)' "$PROJECT_DIR/.env")
fi

BACKUP_DIR="${HOME}/backups/kinkeeper"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="${POSTGRES_DB:-family_notifier}"
DB_USER="${POSTGRES_USER:-family}"

mkdir -p "$BACKUP_DIR"

# 컨테이너 이름(<프로젝트명>-db-1)은 compose 프로젝트명(=디렉터리명)에 좌우되므로
# 하드코딩하지 않고 compose로 db 서비스 컨테이너를 탐색한다(audit #49).
CONTAINER="$(cd "$PROJECT_DIR" && docker compose ps -q db)"
if [ -z "$CONTAINER" ]; then
  echo "❌ db 컨테이너를 찾을 수 없습니다 (docker compose up -d db 필요)" >&2
  exit 1
fi

FINAL_FILE="$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz"
TMP_FILE="${FINAL_FILE}.tmp"

# pg_dump가 중간에 실패하면(DB 재시작·OOM 등) 리다이렉션이 만든 잘린 .gz가 정상 백업으로
# 오인될 수 있다(audit #72). 임시 파일에 먼저 쓰고 성공했을 때만 최종 이름으로 옮기며,
# 실패 시(set -o pipefail로 파이프 실패 감지) 임시 파일을 삭제한다.
trap 'rm -f "$TMP_FILE"' EXIT

docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$TMP_FILE"
mv "$TMP_FILE" "$FINAL_FILE"

# 30일 이상 된 백업 삭제
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete

echo "백업 완료: ${FINAL_FILE}"
