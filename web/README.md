# web

관리자 전용 FastAPI + Jinja2 + HTMX 웹 인터페이스 (HTTP Basic 인증, 루프백 바인딩 + tailscale serve로 노출).

구현된 라우트:
- `/members` — 가족 구성원 CRUD (생일 양력/음력, 자동 생일 규칙 연동)
- `/rules` — 알림 규칙 관리 (생일/명절·기일/커스텀 — 생성 가능 타입은 `_REGISTRY` 기준)
- `/health-checks` — 건강검진 항목·완료 기록 관리
- `/broadcast` — 그룹 채널 수동 공지 발송
- `/healthz` — DB 연결 확인 (정상 200 / 장애 503)

전체 구조는 [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md), 운영은 [docs/OPERATIONS.md](../docs/OPERATIONS.md) 참조.
