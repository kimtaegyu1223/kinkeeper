# KinKeeper

![CI](https://github.com/kimtaegyu1223/kinkeeper/actions/workflows/ci.yml/badge.svg)

가족을 위한 셀프호스팅 텔레그램 알림 봇.  
관리자가 웹 UI에서 구성원과 일정을 관리하면, 예정 시각에 자동으로 텔레그램 채널/DM으로 메시지가 발송됩니다.

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

두 프로세스는 같은 DB를 공유하되 서로 import하지 않습니다. 설계 배경과 알림 파이프라인
상세는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), 운영 절차는
[docs/OPERATIONS.md](docs/OPERATIONS.md)를 참고하세요.

---

## 기능

| 기능 | 설명 |
|---|---|
| 🎂 **생일 알림** | 양력·음력 생일 모두 지원. 규칙별로 며칠 전(리드타임)·발송 시각 설정. 구성원 등록 시 규칙 자동 생성 |
| 🎊 **명절·기일 알림** | 음력 월/일 기반(설날·추석·제사 등). 자동 양력 환산. 리드타임·발송 시각 설정 가능 |
| 📅 **커스텀 일정** | 1회성(특정 일시) 또는 매년 반복(양력/음력). 메시지·발송 시각 자유 설정 |
| 🏥 **건강검진 리마인더** | 항목별 주기/성별/최소나이 조건, 구성원별 주기 오버라이드. 매월 1일 그룹 채널에 그달 안에 도래하는(미수검 포함) 검진 항목을 요약 발송 |
| 📣 **수동 공지** | 관리자가 웹에서 즉시 그룹 채널로 발송 |
| ⚖️ **다이어트/몸무게 트래킹** | 기능 플래그로 **기본 비활성**(`WEIGHT_FEATURE_ENABLED=false`). 켜면 주간 몸무게 입력 DM·nudge·격주 BMI 리포트 DM 동작. 운명 미결(→ [OPERATIONS 미결 사항](docs/OPERATIONS.md#미결-사항)) |

> **건강검진 알림 방식(현행)**: 스케줄된 발송은 **매월 1일 그룹 채널 요약 1건**뿐입니다.
> 개인 DM·검진일 며칠 전 리마인더 같은 별도 예약 발송은 없습니다. 구성원 개인 현황은
> 봇 DM 명령 `/내건강검진`으로 언제든 조회합니다.

### 텔레그램 명령어 (봇 DM)

| 명령어 | 설명 |
|---|---|
| `/start` | 내 텔레그램 ID 확인 (관리자 채널에도 신규 사용자 알림 발송) |
| `/help` | 도움말 |
| `/내생일` | 내 생일까지 남은 일수 확인 |
| `/다음일정 [일수]` | 예정 알림 목록 (일수 생략 시 `SCHEDULE_HORIZON_DAYS` 기본, 1~365 범위) |
| `/내건강검진` | 검진 항목별 현황 (✅ 정상 / ⚠️ 초과 / 🔜 임박 / ❓ 기록 없음) |
| `/검진완료 위내시경 [YYYY-MM-DD]` | 검진 완료 기록 (날짜 생략 시 오늘. 미래 날짜는 거부) |
| `/몸무게 67.2` | 몸무게 기록 (이번 주 nudge 자동 취소). **`WEIGHT_FEATURE_ENABLED=true`일 때만 동작** |

> `/start`·`/help`는 ASCII 슬래시 명령이라 정상적으로 인식됩니다.
> `WEIGHT_FEATURE_ENABLED=false`면 `/help` 목록에서 `/몸무게`가 숨겨지고, 호출해도
> "몸무게 기능은 현재 꺼져 있습니다"로 응답합니다.

> ⚠️ **한글 명령은 봇과의 개인 대화(DM)에서만 안정적으로 동작합니다.**
> 한글 슬래시 명령(`/다음일정` 등)은 텔레그램이 인식하는 ASCII bot_command가 아니라
> 순수 텍스트로 취급됩니다. 텔레그램 봇의 **privacy mode 기본값은 ON**이라, 그룹에서는
> 봇이 이런 순수 텍스트 메시지를 아예 전달받지 못합니다. **그룹에서도 명령을 받으려면**
> [BotFather](https://t.me/BotFather)에서 `/setprivacy` → 해당 봇 선택 → **Disable**로
> privacy mode를 끄세요. (봇을 그룹 관리자로 두면 privacy와 무관하게 전달되기도 합니다.)
> 알림 발송에는 영향이 없으며, DM 명령은 privacy 설정과 무관하게 항상 동작합니다.

---

## 스택

- **Python 3.12** + uv
- **python-telegram-bot v22** (polling)
- **FastAPI** + Jinja2 + HTMX (관리자 웹)
- **PostgreSQL 16** (Docker)
- **SQLAlchemy 2.0** + Alembic
- **APScheduler** (봇 프로세스 내장)
- **systemd `--user`** (서비스 관리)
- structlog, ruff, mypy, pytest + testcontainers

> KinKeeper는 systemd `--user` 유닛 3개(`kinkeeper-bot`, `kinkeeper-web`,
> `kinkeeper-web-tailscale`)로 동작하며 `systemctl --user`로 관리합니다.

---

## 환경변수

`.env` 한 파일에 앱 설정(`shared/config.py`의 `Settings`)과 docker-compose 전용
변수가 함께 들어갑니다. `.env.example`을 복사해 채우세요.

### 앱 설정 (`Settings`)

| 변수 | 기본값 | 필수 | 설명 |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | (빈 값) | ✅ | BotFather 발급 토큰. 비어 있으면 시작 시 중단 |
| `GROUP_CHAT_ID` | `0` | ✅ | 그룹 채널 ID(음수, 예: `-1001234567890`). `0`(미설정)이면 시작 시 중단 |
| `DATABASE_URL` | `postgresql+psycopg://family:changeme@localhost:5432/family_notifier` | | SQLAlchemy 연결 문자열 |
| `ADMIN_USER` | `admin` | | 관리자 웹 HTTP Basic 사용자명 |
| `ADMIN_PASSWORD_HASH` | (빈 값) | | 관리자 웹 비밀번호의 bcrypt 해시 |
| `TZ` | `Asia/Seoul` | | 모든 시각 계산 기준 시간대. 유효한 zoneinfo 이름이 아니면 시작 시 중단 |
| `LOG_LEVEL` | `INFO` | | 로그 레벨 |
| `SCHEDULE_HORIZON_DAYS` | `365` | | 예약 알림을 몇 일 앞까지 미리 생성할지 (`.env`로 조정) |
| `WEIGHT_FEATURE_ENABLED` | `false` | | 다이어트/몸무게 기능 on/off |

> **시작 시 검증**(`Settings.validate_runtime`): 봇·웹 프로세스는 시작할 때
> `TELEGRAM_BOT_TOKEN`(비어 있으면 실패), `GROUP_CHAT_ID`(0이면 실패), `TZ`(유효한
> 시간대가 아니면 실패)를 검사하고, 문제가 있으면 명확한 에러로 즉시 종료합니다.
> import만 하는 테스트/CI는 검증하지 않습니다.

### docker-compose 전용 (앱은 무시)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `POSTGRES_USER` | `family` | Postgres 컨테이너 사용자. install/backup 스크립트도 참조 |
| `POSTGRES_PASSWORD` | `changeme` | Postgres 컨테이너 비밀번호 |
| `POSTGRES_DB` | `family_notifier` | Postgres 데이터베이스명 |

> 앱 `Settings`는 `extra="ignore"`라 `POSTGRES_*`를 조용히 무시합니다.
> `DATABASE_URL`이 실제 앱 연결이고, `POSTGRES_*`는 docker-compose가 컨테이너를
> 초기화할 때만 씁니다. 세 값과 `DATABASE_URL`의 자격증명이 일치해야 합니다.
> **인라인 주석 금지**: 값 뒤에 `#...` 주석을 붙이면 systemd가 값의 일부로 넘겨
> 파싱이 깨집니다.

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

최소한 `TELEGRAM_BOT_TOKEN`, `GROUP_CHAT_ID`, `ADMIN_PASSWORD_HASH`를 채웁니다.
비밀번호 해시는 다음으로 생성합니다:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'비밀번호', bcrypt.gensalt()).decode())"
```

`GROUP_CHAT_ID`는 web.telegram.org에서 그룹을 열었을 때 URL의 `#-100...` 숫자입니다.

### 3. 한 방 설치

```bash
bash deploy/install.sh
```

`deploy/install.sh`가 다음을 순서대로 처리합니다:

1. `uv`·Docker 설치 확인 (없으면 설치)
2. `.env`가 없으면 `.env.example`을 복사하고 편집을 대기, 이후 `.env`를 `chmod 600`
3. `uv sync`로 의존성 설치
4. `docker compose up -d db`로 PostgreSQL 기동 후 **`pg_isready`로 준비 완료까지 폴링**(최대 60초)
5. `alembic upgrade head`로 마이그레이션
6. `loginctl enable-linger`(로그인 세션 없어도 유닛 유지) 후 systemd **`--user` 유닛 3개**
   (`kinkeeper-bot`, `kinkeeper-web`, `kinkeeper-web-tailscale`)를 `~/.config/systemd/user/`에
   설치·enable·restart
7. `kinkeeper-bot`/`kinkeeper-web` 활성 상태 확인

설치 후 관리자 웹은 `http://127.0.0.1:8000`(루프백 전용)에서 뜹니다. 외부 기기에서
접근하려면 SSH 터널이나 tailscale(`kinkeeper-web-tailscale`이 tailnet에 HTTPS로 노출)을
경유하세요. uvicorn `--host`를 `0.0.0.0`으로 바꿔 LAN 전체에 노출하지 마세요.

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

`git pull origin main` → `uv sync` → `alembic upgrade head` → systemd `--user` 유닛 3개
재시작 → 상태 출력을 자동 처리합니다. 마이그레이션이 포함된 배포에서 주의할 점은
[docs/OPERATIONS.md](docs/OPERATIONS.md#배포-절차)를 참고하세요.

---

## 로그 확인

```bash
# 봇 로그 실시간
journalctl --user -u kinkeeper-bot -f

# 웹 로그 실시간
journalctl --user -u kinkeeper-web -f

# 에러만 필터링
journalctl --user -u kinkeeper-bot -p err --since "1 hour ago"
```

---

## 관리자 웹 사용법

### 가족 구성원 등록 (`/members`)

1. **이름** 입력
2. **텔레그램 사용자 ID**: 봇에게 DM으로 `/start` 보내면 확인 가능(없어도 등록 가능 — 그룹 채널 알림만 받음)
3. **생일**: 양력 날짜 또는 음력 월/일 입력(음력은 연도 없이 월 1~12, 일 1~30만 저장. 윤달·음력 2/30은 현재 저장 불가)
4. **성별**: M / F (건강검진 성별 필터에 사용. 미설정이면 성별 지정 항목도 포함)
5. **키(cm)**: 다이어트 기능 활성화 시 BMI 계산에 사용
6. **다이어트 트래킹**: `WEIGHT_FEATURE_ENABLED=true`일 때만 실제로 동작

생일을 입력하면 해당 구성원의 생일 규칙(`reminder_rules`)이 자동으로 생성·갱신됩니다.

### 알림 규칙 등록 (`/rules`)

유형을 선택하면 해당 타입 전용 입력 필드가 나타납니다.

| 유형 | 주요 설정 |
|---|---|
| 🎂 생일 | 대상 구성원, 양력/음력 선택, 발송 시각, 리드타임(며칠 전, 다중) |
| 🎊 명절 | 명절/기일 이름, 음력 월/일, 발송 시각, 리드타임 |
| 📅 커스텀 | 1회성(예약 일시) 또는 매년 반복(양력/음력 월·일), 메시지 직접 입력, 발송 시각 |

> 건강검진·다이어트는 규칙(`/rules`)으로 만들지 않습니다. 각각 전용 화면(`/health`,
> `/diet`)에서 관리하고, 스케줄러가 자동으로 알림을 생성합니다.

규칙을 저장/수정하면 해당 규칙의 예약 알림이 즉시 재생성되고, 매일 새벽 3시에
전체가 재빌드됩니다(미리 생성하는 기간은 `SCHEDULE_HORIZON_DAYS`).

### 건강검진 관리 (`/health`)

건강검진은 규칙 기반이 아닌 별도 시스템입니다.

**검진 항목 관리**
- 항목명, 주기(년), 성별 조건(M/F/전체), 최소 나이
- 예: 위내시경 2년, 여성만 대상 유방암검사, 40세 이상 대장내시경

**구성원별 검진 설정**
- 구성원 상세에서 항목별 개인 주기 오버라이드 가능
- 특정 항목 알림 끄기(`active=False`)
- 검진 기록 직접 추가

**알림 발송 방식(현행)**
- 매월 1일 그룹 채널에, 그달 말 이전에 검진 도래일(미수검 포함)이 걸리는 항목을 구성원별로 묶어 요약 발송
- 검진일 며칠 전 리마인더나 개인 DM 예약 발송은 **없음**. 개인 현황은 `/내건강검진`으로 조회

### 다이어트/몸무게 트래킹 (`/diet`)

**기본 비활성**입니다. `WEIGHT_FEATURE_ENABLED=true`로 켰을 때만 아래가 동작합니다.

구성원별 다이어트 트래킹 활성화(+ 텔레그램 ID·키(cm) 필요) 시:
- 매주 월요일 9시(KST): 몸무게 입력 요청 DM
- 화~일 중 미입력 시: 매일 9시 nudge DM (`/몸무게` 입력 시 이번 주 남은 nudge 자동 취소)
- 격주 화요일 9시: BMI 리포트 DM (발송 직전 최신 몸무게로 생성, 2주 전 대비 변화량 포함)

`WEIGHT_FEATURE_ENABLED=false`면 3시 재빌드가 다이어트 예약 알림을 취소하고,
발송 루프도 남은 `diet:*` 알림을 발송하지 않고 취소합니다.

### 수동 공지 (`/broadcast`)

관리자가 직접 메시지를 작성하여 즉시 그룹 채널에 발송. 발송 기록이 `admin_broadcasts`에 저장됩니다.

---

## 테스트

```bash
uv run pytest -q
```

testcontainers로 실제 PostgreSQL을 띄워 테스트하므로 **Docker가 실행 중이어야 합니다.**

---

## 백업

`deploy/pg_backup.sh`가 db 컨테이너를 `docker compose ps -q db`로 찾아 `pg_dump | gzip`으로
`~/backups/kinkeeper/`에 저장하고, 30일 지난 백업을 삭제합니다. crontab에 등록해 사용:

```bash
# crontab -e 에 추가 (매일 새벽 2시)
0 2 * * * bash /path/to/kinkeeper/deploy/pg_backup.sh
```

복구 절차는 [docs/OPERATIONS.md](docs/OPERATIONS.md#백업복구)를 참고하세요.

---

## 라이선스

MIT
