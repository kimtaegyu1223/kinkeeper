#!/usr/bin/env bash
# PostgreSQL 일일 백업 — crontab에 등록해서 사용
# crontab 예시: 0 2 * * * bash /path/to/kinkeeper/deploy/pg_backup.sh
set -euo pipefail

# .env에서 DB 정보 로드
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/../.env" ]; then
  source <(grep -E '^(POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD)' "$SCRIPT_DIR/../.env")
fi

BACKUP_DIR="${HOME}/backups/kinkeeper"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="${POSTGRES_DB:-family_notifier}"
DB_USER="${POSTGRES_USER:-family}"

mkdir -p "$BACKUP_DIR"

docker exec kinkeeper-db-1 pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz"

# 30일 이상 된 백업 삭제
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete

echo "백업 완료: ${BACKUP_DIR}/${DB_NAME}_${DATE}.sql.gz"
