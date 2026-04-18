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

- Python 3.12, uv
- python-telegram-bot v21 (polling)
- FastAPI + Jinja2 + HTMX (관리자 웹)
- PostgreSQL 16 (docker-compose)
- SQLAlchemy 2.0 + Alembic
- APScheduler (봇 프로세스 내장)
- systemd (운영)

## 아키텍처 개요

```
┌───────────┐         ┌───────────┐
│  bot 프로세스  │         │  web 프로세스  │
│  polling +   │         │  FastAPI +    │
│  scheduler   │         │  HTMX admin   │
└──────┬──────┘         └──────┬──────┘
       │                        │
       └──────── PostgreSQL ────┘
```

두 프로세스는 서로 import하지 않음. 공통 코드는 `shared/`.

## 로컬 개발

```bash
# (작성 예정)
```

## 라이선스

MIT (예정)
