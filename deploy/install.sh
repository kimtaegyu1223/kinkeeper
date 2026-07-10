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
  echo "  ⚠️  Docker 그룹이 현재 셸에는 아직 적용되지 않아, 이후 docker 명령은 sg docker로 감쌉니다."
  echo "     설치가 끝나면 재로그인해야 sudo/sg 없이 docker를 쓸 수 있습니다."
fi

# docker compose 래퍼: 방금 Docker를 설치했거나 유저가 docker 그룹에 갓 추가된 경우
# usermod -aG는 재로그인 전까지 현재 셸에 반영되지 않아 소켓 접근이 거부된다(audit #21).
# 소켓 접근이 안 되면 sg docker로 감싸 docker 그룹 권한으로 실행한다.
dc() {
  if docker info &>/dev/null; then
    docker compose "$@"
  else
    sg docker -c "docker compose $*"
  fi
}

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
dc up -d db

# 고정 sleep은 저사양/느린 디스크에서 initdb가 5초를 넘기면 마이그레이션이 connection refused로
# 실패한다(audit #50). compose healthcheck와 동일한 pg_isready로 준비 완료를 폴링한다.
echo "  → DB 준비 대기 (pg_isready 폴링)..."
if [ -f "$PROJECT_DIR/.env" ]; then
  # shellcheck disable=SC1090
  source <(grep -E '^(POSTGRES_USER|POSTGRES_DB)=' "$PROJECT_DIR/.env")
fi
PG_USER="${POSTGRES_USER:-family}"
PG_DB="${POSTGRES_DB:-family_notifier}"
DB_READY=false
for _ in $(seq 1 30); do
  if dc exec -T db pg_isready -U "$PG_USER" -d "$PG_DB" &>/dev/null; then
    DB_READY=true
    break
  fi
  sleep 2
done
if [ "$DB_READY" != true ]; then
  echo "  ❌ DB가 60초 내에 준비되지 않았습니다" >&2
  exit 1
fi
echo "  → DB 준비 완료"

# ── 5. DB 마이그레이션 ─────────────────────
echo ""
echo "[5/7] DB 마이그레이션..."
uv run alembic upgrade head

# ── 6. systemd --user 서비스 등록 ─────────────────
# deploy.sh가 `systemctl --user`로 재시작하므로 설치도 --user 유닛으로 통일한다(audit #20).
echo ""
echo "[6/7] systemd --user 서비스 등록..."

# 재부팅 후 로그인 세션이 없어도 유닛이 자동 기동/유지되도록 linger 활성화
sudo loginctl enable-linger "$SERVICE_USER"

USER_UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_UNIT_DIR"

# 실행 경로를 현재 환경에 맞게 교체해 유저 유닛 디렉터리에 설치
for svc in kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale; do
  sed \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/deploy/$svc.service" \
    > "$USER_UNIT_DIR/$svc.service"
  echo "  → $USER_UNIT_DIR/$svc.service 등록"
done

systemctl --user daemon-reload
systemctl --user enable kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale
systemctl --user restart kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale

# ── 7. 결과 확인 ──────────────────────────
echo ""
echo "[7/7] 서비스 상태 확인..."
sleep 2
systemctl --user is-active kinkeeper-bot && echo "  ✅ kinkeeper-bot: 실행 중" || echo "  ❌ kinkeeper-bot: 실패"
systemctl --user is-active kinkeeper-web && echo "  ✅ kinkeeper-web: 실행 중" || echo "  ❌ kinkeeper-web: 실패"

echo ""
echo "======================================"
echo " 설치 완료!"
echo ""
echo " 관리자 웹: http://127.0.0.1:8000 (루프백 전용)"
echo "   ⚠️  외부 접근은 tailscale(kinkeeper-web-tailscale) 또는 SSH 터널로만 하세요."
echo "   ⚠️  --host 를 0.0.0.0 으로 바꾸지 마세요 — 관리자 웹이 네트워크 전체에 노출됩니다."
echo " 로그 확인: journalctl --user -u kinkeeper-bot -f"
echo "           journalctl --user -u kinkeeper-web -f"
echo " 업데이트:  bash $PROJECT_DIR/deploy/deploy.sh"
echo "======================================"
