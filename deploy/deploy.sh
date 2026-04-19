#!/usr/bin/env bash
# 서버에서 최신 코드 반영 + 재시작 스크립트
# 사용: bash deploy/deploy.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> git pull"
git pull origin main

echo "==> 의존성 동기화"
~/.local/bin/uv sync

echo "==> DB 마이그레이션"
~/.local/bin/uv run alembic upgrade head

echo "==> 서비스 재시작"
sudo systemctl restart kinkeeper-bot kinkeeper-web

echo "==> 상태 확인"
sudo systemctl status kinkeeper-bot --no-pager
sudo systemctl status kinkeeper-web --no-pager

echo "==> 배포 완료"
