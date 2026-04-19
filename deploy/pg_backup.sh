#!/usr/bin/env bash
# PostgreSQL 일일 백업 — crontab에 등록해서 사용
# crontab 예시: 0 2 * * * bash /home/ktg/projects/kinkeeper/deploy/pg_backup.sh
set -euo pipefail

BACKUP_DIR="/home/ktg/backups/kinkeeper"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="family_notifier"
DB_USER="family"

mkdir -p "$BACKUP_DIR"

pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz"

# 30일 이상 된 백업 삭제
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete

echo "백업 완료: ${BACKUP_DIR}/${DB_NAME}_${DATE}.sql.gz"
