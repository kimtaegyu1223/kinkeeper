# KinKeeper

![CI](https://github.com/kimtaegyu1223/kinkeeper/actions/workflows/ci.yml/badge.svg)

가족을 위한 셀프호스팅 텔레그램 알림 봇.  
관리자가 웹 UI에서 일정을 등록하면, 예정 시각에 자동으로 텔레그램 채널에 메시지가 발송됩니다.

```
┌─────────────────────┐       ┌─────────────────────┐
│    bot 프로세스       │       │    web 프로세스       │
│  polling +          │       │  FastAPI +           │
│  APScheduler        │       │  HTMX 관리자 UI       │
└──────────┬──────────┘       └──────────┬───────────┘
           │                             │
           └──────── PostgreSQL ─────────┘
                      shared/
```

두 프로세스는 같은 DB를 공유하되 서로 import하지 않습니다.

---

## 기능

| 기능 | 설명 |
|---|---|
| 🎂 **생일 알림** | 양력·음력 생일 모두 지원. 다단계 리드타임(2주/1주/3일/1일/당일) |
| 🎊 **명절 알림** | 설날·추석 등 음력 자동 환산. 한 달 전 교통편 예매 리마인드 포함 |
| 🏥 **건강검진 알림** | 연·반기 주기 리마인더 |
| ⚖️ **다이어트 리포트** | 가족이 `/몸무게 67.2` 입력 → 주간/월간 리포트 자동 발송 |
| 📅 **커스텀 일정** | 1회성 또는 매년 반복, 메시지·발송 시각 자유 설정 |
| 📣 **수동 공지** | 관리자가 웹에서 즉시 채널 발송 |

### 텔레그램 명령어 (봇 DM)

| 명령어 | 설명 |
|---|---|
| `/start` | 시작 |
| `/help` | 도움말 |
| `/내생일` | 내 생일까지 남은 일수 확인 |
| `/다음일정` | 앞으로 7일 이내 예정 알림 목록 |
| `/몸무게 67.2` | 몸무게 기록 |

---

## 스택

- **Python 3.12** + uv
- **python-telegram-bot v21** (polling)
- **FastAPI** + Jinja2 + HTMX (관리자 웹)
- **PostgreSQL 16** (Docker)
- **SQLAlchemy 2.0** + Alembic
- **APScheduler** (봇 프로세스 내장)
- **systemd** (서비스 관리)
- structlog, ruff, mypy, pytest + testcontainers

---

## 빠른 시작 (서버 최초 설치)

### 사전 준비

- Ubuntu 22.04 / 24.04
- 텔레그램 봇 토큰 ([BotFather](https://t.me/BotFather)에서 발급)
- 텔레그램 채널 또는 그룹 (봇을 관리자로 초대)

### 1. 저장소 클론

```bash
git clone https://github.com/kimtaegyu1223/kinkeeper.git
cd kinkeeper
```

### 2. .env 파일 작성

```bash
cp .env.example .env
nano .env
```

`.env` 필수 항목:

```ini
# BotFather에서 발급한 토큰
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 텔레그램 채널/그룹 ID (음수, 예: -1001234567890)
# 확인 방법: web.telegram.org 에서 그룹 열기 → URL의 #-100... 숫자
GROUP_CHAT_ID=-1001234567890

# DB 연결 (docker-compose 기본값 그대로 사용 가능)
DATABASE_URL=postgresql+psycopg://family:changeme@localhost:5432/family_notifier
POSTGRES_USER=family
POSTGRES_PASSWORD=changeme
POSTGRES_DB=family_notifier

# 관리자 계정
ADMIN_USER=admin
# 비밀번호 해시 생성:
# python3 -c "import bcrypt; print(bcrypt.hashpw(b'비밀번호', bcrypt.gensalt()).decode())"
ADMIN_PASSWORD_HASH=$2b$12$...

# 기타
TZ=Asia/Seoul
LOG_LEVEL=INFO
SCHEDULE_HORIZON_DAYS=60
```

### 3. 한 방 설치

```bash
bash deploy/install.sh
```

설치 스크립트가 다음을 자동으로 처리합니다:
- uv, Docker 설치 확인
- Python 의존성 설치
- PostgreSQL 컨테이너 기동 (docker-compose)
- DB 마이그레이션
- systemd 서비스 등록 및 시작

설치 완료 후 `http://서버IP:8000` 으로 관리자 웹 접속.

---

## 로컬 개발

```bash
# 의존성 설치
uv sync

# PostgreSQL 기동
docker compose up -d db

# DB 마이그레이션
uv run alembic upgrade head

# 봇 실행
uv run python -m bot.main

# 웹 실행 (새 터미널)
uv run uvicorn web.main:app --host 127.0.0.1 --port 8000 --reload
```

관리자 웹: http://localhost:8000

---

## 업데이트 배포

```bash
bash deploy/deploy.sh
```

`git pull` → `uv sync` → `alembic upgrade head` → 서비스 재시작을 자동 처리합니다.

---

## 로그 확인

```bash
# 봇 로그 실시간
journalctl -u kinkeeper-bot -f

# 웹 로그 실시간
journalctl -u kinkeeper-web -f

# 에러만 필터링
journalctl -u kinkeeper-bot -p err --since "1 hour ago"
```

---

## 관리자 웹 사용법

### 가족 구성원 등록 (`/members`)

1. **이름** 입력
2. **텔레그램 사용자 ID**: 봇에게 DM으로 `/start` 보내면 확인 가능. 또는 [@userinfobot](https://t.me/userinfobot)에게 `/start`
3. **생일**: 양력 또는 음력(월/일) 입력

### 알림 규칙 등록 (`/rules`)

유형을 선택하면 해당 타입 전용 입력 필드가 나타납니다.

| 유형 | 주요 설정 |
|---|---|
| 🎂 생일 | 대상 구성원, 양력/음력 선택, 알림 시각, 며칠 전 |
| 🎊 명절 | 명절 이름, 음력 월/일, 알림 시각, 며칠 전 |
| 🏥 건강검진 | 대상 구성원, 주기(연/반기), 기준 날짜, 알림 시각 |
| ⚖️ 다이어트 | 주기(주/월), 요일, 발송 시각 |
| 📅 커스텀 | 1회성 또는 매년 반복, 메시지 직접 입력, 발송 시각 |

규칙을 저장하면 앞으로 60일치 알림이 자동으로 예약됩니다. 매일 새벽 3시에 재빌드됩니다.

---

## 테스트

```bash
uv run pytest -v
```

testcontainers로 실제 PostgreSQL을 띄워 테스트합니다 (Docker 필요).

---

## 백업

```bash
# crontab -e 에 추가 (매일 새벽 2시)
0 2 * * * bash /home/ktg/projects/kinkeeper/deploy/pg_backup.sh
```

---

## 라이선스

MIT
