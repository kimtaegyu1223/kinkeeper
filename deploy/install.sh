#!/usr/bin/env bash
# KinKeeper 최초 설치 스크립트 (Ubuntu 22.04 / 24.04)
# 사용: bash deploy/install.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="$(whoami)"

echo "======================================"
echo " KinKeeper 설치 시작"
echo " 프로젝트 경로: $PROJECT_DIR"
echo " 실행 유저: $SERVICE_USER"
echo "======================================"

# ── 1. 필수 패키지 ───────────────────────
echo ""
echo "[1/7] 필수 패키지 확인..."
if ! command -v uv &>/dev/null; then
  echo "  → uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v docker &>/dev/null; then
  echo "  → Docker 설치 중..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$SERVICE_USER"
  echo "  ⚠️  Docker 그룹 적용을 위해 로그아웃 후 재로그인 필요할 수 있음"
fi

# ── 2. .env 파일 확인 ─────────────────────
echo ""
echo "[2/7] .env 파일 확인..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "  → .env.example 복사 완료"
  echo ""
  echo "  ⚠️  .env 파일을 먼저 편집하세요:"
  echo "     nano $PROJECT_DIR/.env"
  echo ""
  echo "  필수 항목:"
  echo "    TELEGRAM_BOT_TOKEN  : BotFather에서 발급"
  echo "    GROUP_CHAT_ID       : 텔레그램 채널/그룹 ID (예: -1001234567890)"
  echo "    ADMIN_PASSWORD_HASH : 아래 명령어로 생성"
  echo ""
  echo "  비밀번호 해시 생성:"
  echo "    python3 -c \"import bcrypt; print(bcrypt.hashpw(b'비밀번호', bcrypt.gensalt()).decode())\""
  echo ""
  read -rp "  .env 편집 완료 후 Enter를 누르세요..."
fi
chmod 600 "$PROJECT_DIR/.env"

# ── 3. 의존성 설치 ────────────────────────
echo ""
echo "[3/7] Python 의존성 설치..."
cd "$PROJECT_DIR"
uv sync

# ── 4. PostgreSQL (docker-compose) ────────
echo ""
echo "[4/7] PostgreSQL 기동..."
docker compose up -d db
echo "  → 컨테이너 준비 대기 (5초)..."
sleep 5

# ── 5. DB 마이그레이션 ─────────────────────
echo ""
echo "[5/7] DB 마이그레이션..."
uv run alembic upgrade head

# ── 6. systemd 서비스 등록 ─────────────────
echo ""
echo "[6/7] systemd 서비스 등록..."

# 실행 경로와 유저를 현재 환경에 맞게 교체
for svc in kinkeeper-bot kinkeeper-web; do
  sed \
    -e "s|User=ktg|User=$SERVICE_USER|g" \
    -e "s|/home/ktg/projects/kinkeeper|$PROJECT_DIR|g" \
    "$PROJECT_DIR/deploy/$svc.service" \
    | sudo tee "/etc/systemd/system/$svc.service" > /dev/null
  echo "  → /etc/systemd/system/$svc.service 등록"
done

sudo systemctl daemon-reload
sudo systemctl enable kinkeeper-bot kinkeeper-web
sudo systemctl restart kinkeeper-bot kinkeeper-web

# ── 7. 결과 확인 ──────────────────────────
echo ""
echo "[7/7] 서비스 상태 확인..."
sleep 2
sudo systemctl is-active kinkeeper-bot && echo "  ✅ kinkeeper-bot: 실행 중" || echo "  ❌ kinkeeper-bot: 실패"
sudo systemctl is-active kinkeeper-web && echo "  ✅ kinkeeper-web: 실행 중" || echo "  ❌ kinkeeper-web: 실패"

echo ""
echo "======================================"
echo " 설치 완료!"
echo ""
echo " 관리자 웹: http://$(hostname -I | awk '{print $1}'):8000"
echo " 로그 확인: journalctl -u kinkeeper-bot -f"
echo "           journalctl -u kinkeeper-web -f"
echo " 업데이트:  bash $PROJECT_DIR/deploy/deploy.sh"
echo "======================================"
