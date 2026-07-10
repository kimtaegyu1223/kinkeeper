#!/usr/bin/env bash
# 서버에서 최신 코드 반영 + 재시작 스크립트
# 사용: bash deploy/deploy.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 파괴적 마이그레이션(컬럼 드롭 등) 대비 stop→migrate→start 순서로 배포한다.
# restart 방식은 마이그레이션이 컬럼을 드롭한 직후~재시작 완료 전까지 구코드가 계속
# 돌며 드롭된 컬럼을 SELECT해 500/스케줄러 예외를 낼 수 있다(expand/contract 위반).
# 서비스를 먼저 정지하면 그 겹침 창이 사라진다. 다운타임 몇 초는 허용한다.
echo "==> 서비스 정지"
systemctl --user stop kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale

echo "==> git pull"
git pull origin main

echo "==> 의존성 동기화"
~/.local/bin/uv sync

echo "==> DB 마이그레이션"
~/.local/bin/uv run alembic upgrade head

echo "==> 서비스 시작"
systemctl --user start kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale

echo "==> 상태 확인"
systemctl --user status kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale --no-pager

echo "==> 배포 완료"
