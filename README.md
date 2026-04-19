# kinkeeper

A self-hosted family assistant bot that keeps track of birthdays, holidays, health check-ups, diet reports, and more — delivered via Telegram.

관리자가 웹으로 일정을 등록하면 예정 시각에 가족 구성원에게 텔레그램 메시지가 전송됩니다.

## 기능

- **생일 알림**: 구성원별 생일, 다단계 리드타임(2주/1주/3일/1일 전)
- **명절 알림**: 설날·추석 음력 자동 환산, 한 달 전 + 이틀 전(예매 리마인드)
- **건강검진 알림**: 주기 기반 리마인더
- **수동 공지**: 관리자가 대상 선택해 즉시 발송
- **다이어트 리포트**: `/몸무게 67.2` 입력, 주간/월간 리포트 자동 발송
- **커스텀 일정**: 관리자가 자유롭게 추가

## 스택

- Python 3.12 + uv
- python-telegram-bot v21 (polling)
- FastAPI + Jinja2 + HTMX (관리자 웹)
- PostgreSQL 16
- SQLAlchemy 2.0 + Alembic
- APScheduler (봇 프로세스 내장)
- systemd (운영 서버)

## 아키텍처 개요

```
┌─────────────────┐       ┌─────────────────┐
│   bot 프로세스    │       │   web 프로세스    │
│  polling +      │       │  FastAPI +       │
│  APScheduler    │       │  HTMX admin UI   │
└────────┬────────┘       └────────┬─────────┘
         │                         │
         └──────── PostgreSQL ──────┘
                  (shared/)
```

두 프로세스는 서로 import하지 않음. 공통 코드는 `shared/`.

## 로컬 개발

### 사전 준비

```bash
# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh

# docker-compose-plugin 설치 (PostgreSQL용)
sudo apt install docker-compose-plugin -y
```

### 실행

```bash
# 1. 저장소 클론
git clone https://github.com/kimtaegyu1223/kinkeeper.git
cd kinkeeper

# 2. .env 파일 생성
cp .env.example .env
# .env에 TELEGRAM_BOT_TOKEN, ADMIN_PASSWORD_HASH 입력

# 관리자 비밀번호 해시 생성
python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"

# 3. 의존성 설치
uv sync

# 4. PostgreSQL 기동
docker compose up -d db

# 5. DB 마이그레이션
uv run alembic upgrade head

# 6. 봇 실행
uv run python -m bot.main

# 7. 웹 실행 (새 터미널)
uv run uvicorn web.main:app --host 127.0.0.1 --port 8000 --reload
# http://localhost:8000 접속
```

## 운영 서버 배포 (Ubuntu)

### PostgreSQL 설치

```bash
sudo apt install postgresql postgresql-contrib -y
sudo -u postgres createuser --pwprompt family
sudo -u postgres createdb -O family family_notifier
```

### systemd 서비스 등록

```bash
sudo cp deploy/kinkeeper-bot.service /etc/systemd/system/
sudo cp deploy/kinkeeper-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kinkeeper-bot kinkeeper-web
```

### 로그 확인

```bash
journalctl -u kinkeeper-bot -f
journalctl -u kinkeeper-web -f
```

### 업데이트 배포

```bash
bash deploy/deploy.sh
```

### 일일 백업 설정

```bash
crontab -e
# 추가: 0 2 * * * bash /home/ktg/projects/kinkeeper/deploy/pg_backup.sh
```

## 테스트

```bash
uv run pytest -v
```

## 라이선스

MIT
