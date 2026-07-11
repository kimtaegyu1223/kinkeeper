# KinKeeper 아키텍처

이 문서는 KinKeeper의 프로세스 구성·데이터 모델·알림 파이프라인을 **현재 코드 기준**으로
설명합니다.  
대상 독자: 코드베이스를 처음 보는 개발자, 또는 몇 달 뒤에 "이거 어떻게 돌아갔지?"를
다시 확인하려는 본인.

---

## 1. 프로세스 토폴로지

세 개의 systemd `--user` 유닛과 하나의 Docker 컨테이너가 함께 동작합니다.

```
┌──────────────────────────────────┐
│  kinkeeper-bot                    │
│    python -m bot.main             │
│    python-telegram-bot (polling)  │
│    APScheduler (같은 asyncio loop) │
│    handlers/ — 봇 명령 처리         │
│    scheduler.py — 발송·재빌드 잡     │
└────────────────┬─────────────────┘
                 │
      PostgreSQL 16 (Docker: db)
        shared/models.py
        shared/generators/
                 │
┌────────────────┴─────────────────┐
│  kinkeeper-web                    │
│    uvicorn web.main:app           │
│      --host 127.0.0.1 --port 8000 │
│    FastAPI + Jinja2 + HTMX         │
│    routes/ — 관리자 CRUD            │
│    동기 SQLAlchemy                  │
└──────────────────────────────────┘

  kinkeeper-web-tailscale
    uvicorn web.main:app --host 100.101.209.52 --port 8000
    (같은 앱을 tailnet 전용 IP에 바인딩한 별도 인스턴스로 노출)
```

- **`kinkeeper-bot`** — `bot/main.py`의 `main()`. structlog(JSON) 설정 →
  `settings.validate_runtime()` → APScheduler 생성 → PTB `Application` 빌드 →
  `run_polling(drop_pending_updates=True)`. `post_init`에서 스케줄러 시작·시작 시
  rebuild(실패해도 폴링은 계속)·privacy mode 경고 로깅을 수행합니다.
- **`kinkeeper-web`** — `uvicorn`이 `web.main:app`을 **127.0.0.1:8000**에만 바인딩.
  lifespan에서 `settings.validate_runtime()`을 호출합니다. HTTP Basic 인증,
  Sec-Fetch-Site 기반 최소 CSRF 방어, request_id 로깅, `IntegrityError`→400 변환,
  `/healthz`를 포함합니다.
- **`kinkeeper-web-tailscale`** — 같은 앱을 tailnet 전용 IP(100.101.209.52:8000)에
  바인딩한 별도 uvicorn 인스턴스. LAN·공인망에는 노출하지 않는다.
  `tailscale serve` 프록시(단일 인스턴스)로 전환하려면 1회성으로
  `sudo tailscale set --operator=<user>`가 필요 — 유닛 파일 주석 참조.
- **PostgreSQL** — docker-compose의 `db` 서비스(`postgres:16-alpine`).
  **`127.0.0.1:5432`에만** 포트 매핑, healthcheck는 `pg_isready`.

**왜 봇/웹을 두 프로세스로 분리했는가?**  
봇은 `asyncio` 이벤트 루프 위에서 동작하고 APScheduler도 같은 루프에 붙습니다. 웹은
FastAPI지만 SQLAlchemy를 **동기**로 씁니다. 합치면 스레드/코루틴 경계가 복잡해지므로,
서로 import하지 않는 규칙을 지켜 각 프로세스를 독립적으로 이해·재시작합니다.
`shared/`는 두 프로세스가 공통으로 쓰는 코드(모델·DB 세션·제너레이터·notifier)만 담습니다.

---

## 2. 알림 파이프라인

핵심 흐름은 **규칙/상태 → 재빌드 → 예약 큐 → 발송**입니다.

```
reminder_rules            건강검진 상태          다이어트 상태(플래그 on일 때만)
(생일/명절/커스텀)         (기록·주기)            (diet_active·몸무게)
      │                        │                        │
      └──────────┬─────────────┴────────────────────────┘
                 ▼
        rebuild (03시 cron + 규칙 웹 편집 시 즉시)
        horizon = settings.schedule_horizon_days
                 │  ON CONFLICT로 부분 유니크 인덱스에 dedup 위임
                 ▼
        scheduled_notifications  (pending / sent / failed / cancelled)
                 │
                 ▼
        dispatch (1분 틱: scheduled_at<=now AND status=pending)
        24h 초과 pending 취소 · 재시도/429는 notifier가 처리
```

### 2.1 재빌드 (생성)

`bot/scheduler.py`의 `_do_rebuild()`가 한 세션 안에서 순서대로 호출합니다:

1. `rebuild_upcoming(session, horizon_days=settings.schedule_horizon_days)`
   — 규칙 기반(생일/명절/커스텀)
2. `rebuild_health_checks(session, horizon_days=...)` — 건강검진(상태 기반)
3. `WEIGHT_FEATURE_ENABLED`가 **true면** `rebuild_diet_reports(...)`,
   **false면** `_cancel_pending_diet_notifications()`로 남은 `diet:%` pending을 취소
4. `_purge_old_notifications()` — 90일(`_RETENTION`) 지난 종료 상태(sent/failed/cancelled)
   행을 물리 삭제

재빌드 트리거는 두 가지입니다:

- **매일 새벽 3시 cron** — `create_scheduler()`가 `settings.tz` 타임존으로
  `cron hour=3` 잡을 등록(`misfire_grace_time=3600`, `coalesce=True`). 봇 프로세스
  시작 시에도 1회 `_startup_rebuild()`가 돕니다(DB 미기동 시 지수 백오프 재시도,
  끝내 실패해도 폴링·03시 cron이 보충).
- **규칙 웹 편집 시 즉시** — `web/routes/rules.py`가 규칙 생성/수정 후
  `rebuild_for_rule(rule_id, session)`으로 해당 규칙 큐만 즉시 재생성.

**규칙 기반 제너레이터** (`shared/generators/{birthday,holiday,custom}.py`):
`ReminderRule`을 읽어 `lead_times_days`만큼 `scheduled_at`을 계산하고
`upsert_notification()`으로 insert합니다. `rebuild_upcoming()`은 재생성 직전
rule 기반 pending을 **물리 삭제**(`_delete_pending_rule_notifications`)한 뒤 활성 규칙을
다시 순회하며, 규칙 하나의 예외가 전체를 중단시키지 않도록 규칙별로 격리합니다.
규칙 삭제 시에는 FK `ON DELETE CASCADE`로 연관 알림도 함께 삭제됩니다.

**상태 기반 제너레이터** (`health_check.py`, `diet_report.py`): DB의 현재 상태(검진
기록·주기·몸무게·`diet_active`)를 직접 순회해 알림을 계산하고 `source_key` upsert로
멱등하게 유지합니다. 이번 재빌드가 원하지 않게 된 `hc:%`/`diet:%` pending은 각
`_cancel_stale_*` 함수가 `cancelled`로 바꿉니다.

### 2.2 예약 큐: `scheduled_notifications` 상태와 유니크 인덱스

상태는 `NotificationStatus` enum입니다: **`pending` / `sent` / `failed` / `cancelled`**.
(주의: 상태 이름은 `failed`이고, 실패 사유 문자열은 별도의 `error` 컬럼에 담깁니다.)

새벽 재빌드·웹 편집·봇 발송이 겹칠 때 같은 slot이 중복 insert되는 것을 **DB 레벨의
부분 유니크 인덱스 2개**로 막습니다(둘 다 `status='pending'`일 때만 강제 — 이력은
여러 건 남을 수 있음):

| 인덱스 | 대상 컬럼 | 술어 |
|---|---|---|
| `uq_sched_notif_rule_pending` | `(rule_id, scheduled_at, target_telegram_id)` | `status='pending' AND rule_id IS NOT NULL` |
| `uq_sched_notif_source_pending` | `(source_key)` | `status='pending' AND source_key IS NOT NULL` |

제너레이터는 앱단 SELECT 대신 이 인덱스에 dedup을 **위임**합니다
(`shared/generators/base.py`):

- `upsert_notification()` — rule 알림. `ON CONFLICT ... DO NOTHING`
  (인덱스 술어와 동일한 `index_where`로 rule 인덱스를 대상 지정).
- `upsert_notification_by_key()` — source_key 알림. `ON CONFLICT ... DO UPDATE`로
  기존 pending의 `scheduled_at`/`target`/`message`를 갱신(리빌드마다 내용이 달라질 수
  있으므로). rule 없는 모든 알림(건강검진·다이어트)이 이 한 규칙을 공유합니다.

### 2.3 발송: `dispatch_pending` (1분 틱)

`create_scheduler()`가 `interval minutes=1`로 `dispatch_pending`을 등록합니다
(`misfire_grace_time=59`). 동작:

```sql
SELECT * FROM scheduled_notifications
WHERE scheduled_at <= now()  -- UTC
  AND status = 'pending'
ORDER BY scheduled_at
LIMIT 50
```

각 행에 대해(한 행의 예외가 배치를 멈추지 않도록 개별 try로 격리):

1. **staleness 취소** — `now - scheduled_at > 24h`(`_STALE_AFTER`)면 발송하지 않고
   `cancelled`. 다운타임 후 묵은 알림이 폭주하는 것을 막습니다.
2. **다이어트 플래그** — `source_key`가 `diet:`로 시작하고 `WEIGHT_FEATURE_ENABLED`가
   false면 `cancelled`.
3. **nudge 재확인** — `diet:nudge:{member_id}:...`면 이번 주(KST 월~일) 몸무게 기록이
   이미 있는지 확인해 있으면 `cancelled`.
4. **BMI placeholder 해석** — `message`가 `__bmi_report__:{member_id}`면 발송 직전
   `build_bmi_report()`로 최신 몸무게 기반 메시지를 생성. 멤버 삭제/키 미등록 등으로
   실패하면 placeholder 원문이 나가지 않도록 `cancelled`.
5. `send_message(chat_id, message)` 호출 후, **아직 `pending`인 행만** 조건부 UPDATE로
   `sent`(성공, `sent_at` 기록) 또는 `failed`(실패, `error`에 사유)로 갱신
   (`_mark_sent`). fetch~발송 사이에 웹 재빌드가 행을 바꿨을 수 있어 `status='pending'`
   조건을 답니다.

**재시도·429는 `send_message`(notifier) 안에서** 처리합니다(§4).

**왜 규칙과 큐를 분리했는가?**  
규칙은 "무엇을 언제"의 정의이고, 스케줄러는 규칙을 해석하지 않고 큐만 폴링합니다.
규칙 수정 시 `rebuild_for_rule`로 해당 큐만 멱등하게 재생성하고, 발송 이력(`sent_at`,
`error`)이 큐에 남아 추적 가능합니다. 이 구조가 발송 로직과 생성 로직을 분리합니다.

---

## 3. 시간대·음력

### 시간대 규칙 (`shared/generators/_time.py`)

타임존 로직을 한 모듈에 모아 생성기별 복붙 버그를 막습니다. **모든 로컬 날짜 계산은
`settings.tz`(기본 KST) 기준**이고, DB에는 UTC(`DateTime(timezone=True)`)로 저장합니다.

- `today_local()` — `settings.tz` 기준 오늘 날짜
- `now_utc()` — 현재 UTC
- `scheduled_at_local(day, hour=9)` — 로컬 벽시계 → UTC 변환

APScheduler cron(03시)도 `settings.tz`로 발화해 생성기의 로컬 날짜 계산과 어긋나지
않게 합니다. 다이어트 주 경계(월~일)와 `/몸무게` nudge 취소 창도 KST 기준으로 계산합니다.

### 음력 처리 (`shared/lunar.py`)

`korean_lunar_calendar`의 `KoreanLunarCalendar`로 음력→양력을 변환합니다.
`lunar_to_solar(year, month, day)`는 존재하지 않는 날짜면 `None`을 반환합니다
(라이브러리가 `'0000-00-00'`을 truthy로 돌려주므로 명시적으로 걸러냅니다).

생성기(생일/명절/커스텀)는 **연도 후보 `today.year-1 .. today.year+1`을 모두 시도**합니다.
음력은 매년 양력 날짜가 달라지고, 음력 11~12월 날짜는 이듬해 양력 1~2월로 떨어져
연초에는 전년도 음력 연도가 필요하기 때문입니다.

> **윤달 미지원.** `lunar_to_solar`는 평달만 다루고 윤달 플래그를 받지 않습니다.
> 음력 생일은 연도 없는 자리표시자(`date(2000, m, d)`)로 저장하므로 윤달·음력 2/30을
> 표현할 수 없어, 웹 입력 단계에서 400으로 거부합니다. (§6 알려진 한계)

---

## 4. 텔레그램 전송 (`shared/notifier.py`)

봇·웹의 모든 발송이 거치는 단일 지점 `send_message(chat_id, text) -> (bool, error)`.
`parse_mode="HTML"`로 보내며(그래서 자유 입력값은 생성기에서 `html.escape`),
동작은:

- **재시도** — 타임아웃·네트워크 순단·5xx는 지수 백오프(1s, 2s)로 최대 3회
  (`_MAX_ATTEMPTS`).
- **429** — body의 `parameters.retry_after` 또는 `Retry-After` 헤더를 존중해 대기 후
  재시도. dispatch 루프가 막히지 않도록 상한 60초(`_RETRY_AFTER_CAP`).
- **그 외 4xx**(400 chat not found·403 봇 차단 등) — 영구 오류로 보고 재시도 없이 중단.
- **토큰 마스킹** — 반환·로깅되는 에러 문자열에서 봇 토큰과 `/bot<token>` URL을 `***`로
  마스킹합니다.

---

## 5. 데이터 모델 개요 (`shared/models.py`)

| 테이블 | 역할 | 메모 |
|---|---|---|
| `family_members` | 구성원 마스터 | `telegram_user_id` nullable(계정 없어도 등록·그룹 알림만 수신), `gender` M/F/None(None=미설정→성별 지정 항목도 포함), `birthday_solar`/`birthday_lunar` 독립 nullable, `height_cm`·`diet_active` |
| `reminder_rules` | 규칙 정의 | `type`(birthday/holiday/custom 등), `lead_times_days`(int 배열), **`config` JSONB**, `active` |
| `scheduled_notifications` | 발송 큐 | `rule_id`(nullable, CASCADE) 또는 `source_key`(rule 없는 알림), `scheduled_at`(UTC), `status`, `sent_at`, `error`. 부분 유니크 인덱스 2개(§2.2) |
| `weight_logs` | 몸무게 append-only | 최신 1건 `ORDER BY recorded_at DESC LIMIT 1`, BMI에서 2주 전과 비교 |
| `admin_broadcasts` | 수동 공지 이력 | `sent_by`, `message`, `sent_at` |
| `health_check_types` | 검진 항목(글로벌) | `name`(unique), `period_years`, `gender`, `min_age`, `active` |
| `health_check_records` | 검진 완료 기록 | `(member_id, check_type_id, checked_at)` unique |
| `member_health_check_configs` | 구성원별 오버라이드 | `period_years`(None=type 기본), `active`(False=이 사람 이 항목 끔). `(member_id, check_type_id)` unique |

### `config` JSONB (`shared/config_schemas.py`)

`reminder_rules.config`는 type마다 형태가 다릅니다. **형태의 단일 출처는
`config_schemas.py`의 TypedDict**(`BirthdayConfig`/`HolidayConfig`/`CustomConfig`,
모두 `total=False`)입니다. 런타임 검증은 하지 않고(관리자 1명이 웹 폼으로만 작성),
생성기는 `.get(key, default)`로 안전하게 읽으며, 정적으로는 mypy가 키 오타를 잡습니다.
JSONB를 쓰는 이유: 유형마다 필요한 데이터가 다르고(생일=구성원 id, 명절=음력 월/일,
커스텀=repeat/run_at 등), 유형별 테이블로 쪼개면 유형 추가 때 마이그레이션 비용이
생기기 때문입니다.

### `source_key` — rule 없는 알림 경로

건강검진·다이어트는 `rule_id`가 없으므로 CASCADE 삭제가 불가능합니다. 대신
`source_key`로 멱등 upsert하고, 대상에서 빠진 묵은 알림은 `_cancel_stale_*`가
취소합니다. 현재 코드가 실제로 만드는 키:

```
hc:monthly:group:{report_date}                # 건강검진 월간 그룹 리포트 (유일한 hc 키)
diet:remind:{member_id}:{monday}              # 주간 몸무게 입력 요청 DM
diet:nudge:{member_id}:{nudge_date}           # 미입력 nudge DM (화~일)
diet:bmi:{member_id}:{report_date}            # 격주 BMI 리포트 DM (화요일)
```

---

## 6. 건강검진 시스템 (`shared/generators/health_check.py`)

**규칙 기반이 아닌 이유**: 건강검진 예정일은 "마지막 검진일 + 주기"로 동적으로 계산되고,
기록이 추가될 때마다 바뀝니다. 날짜가 고정된 이벤트에 맞는 규칙 시스템과는 맞지 않아
상태를 직접 순회합니다.

**필터·주기 결정** (`_collect_report_items`, 활성 구성원 × 활성 검진 항목):

1. **성별** — `ct.gender`와 `member.gender`가 둘 다 설정됐고 불일치면 스킵.
   `member.gender`가 None(미설정)이면 "모르니까 포함"(누락보다 과다가 안전).
2. **최소 나이** — `min_age`가 있으면, 생일을 전혀 모르면 스킵. **양력 생일이 있을 때만**
   나이를 계산해 필터하고, 음력만 있으면(연도가 센티널이라 나이 미상) 보수적으로 포함.
3. **구성원 config** — `config.active=False`면 스킵. 주기는
   `config.period_years or ct.period_years`.
4. **due_date** — 기록 없으면 `today`(지금 검진 필요), 있으면 `마지막 검진일 + 주기`.

**발송(현행)**: `rebuild_health_checks`는 오늘(또는 다음 달 1일)부터 horizon까지 각 달의
1일에 대해, **그달 말(다음 달 1일 이전)에 due_date가 걸리는 항목**(미수검=과거 due 포함)을
구성원별로 묶어 `hc:monthly:group:{1일}` 그룹 채널 리포트로 예약합니다. 검진일 며칠 전
리마인더나 개인 DM 예약 발송은 없습니다.

봇 DM `/내건강검진`(`bot/handlers/health.py`)은 위와 **같은 성별·나이 필터**를 써서
항목별 현황(✅ 정상 / 🔜 30일 이내 / ⚠️ 초과 / ❓ 기록 없음)을 조회로 보여줍니다.
`/검진완료`는 기록을 추가하되 미래 날짜는 거부합니다.

---

## 7. 다이어트 트래킹 (`shared/generators/diet_report.py`)

**`WEIGHT_FEATURE_ENABLED=true`일 때만** 재빌드·발송됩니다(§2.1). 대상은 `active` ∧
`diet_active` ∧ `telegram_user_id` 있음 ∧ `height_cm` 있음인 구성원.

```
매주 월요일 09:00  → diet:remind  (몸무게 입력 요청 DM)
화~일 09:00        → diet:nudge   (그 주 기록 없으면; /몸무게 입력 시 취소)
격주 화요일 09:00   → diet:bmi     (발송 직전 build_bmi_report로 생성)
```

- **nudge 취소**: `/몸무게` 입력 시 `weight.py`가 이번 주(KST 월~일) `diet:nudge:{id}:%`
  pending을 `cancelled`로 바꿉니다. dispatch도 발송 직전 그 주 기록을 재확인합니다.
- **격주 판정**: 고정 epoch(`1970-01-05`, 월요일) 기준 절대 주차의 짝/홀로 판정해,
  리빌드 실행 주와 무관하게 항상 같은 주에만 발송합니다.
- **BMI 리포트**: 예약은 "2주 뒤"지만 메시지는 발송 시점 최신 몸무게를 반영해야 하므로
  placeholder(`__bmi_report__:{id}`)로 예약하고 dispatch에서 실시간 생성합니다
  (18.5 미만 저체중 / 18.5~22.9 정상 / 23.0~24.9 과체중 / 25.0↑ 비만, 정상 범위
  `18.5·h²`~`22.9·h²`, 2주 전 대비 증감 포함).

---

## 8. 트레이드오프 & 알려진 한계

### 의도적 배제

| 결정 | 이유 |
|---|---|
| **APScheduler, Redis/Celery 없음** | 가족 규모에서 1분 폴링으로 충분. Celery는 브로커·워커·모니터링까지 복잡 |
| **동기 SQLAlchemy(웹)** | async 세션 관리가 더 복잡. 가족 봇 쿼리 수에서 동기로 충분 |
| **polling, webhook 없음** | 공인 IP+HTTPS 없이 동작. private 가족 봇에 충분 |
| **봇·웹 분리** | 봇은 asyncio, 웹은 동기 FastAPI. 합치면 스레드 경계가 복잡 |
| **Docker는 DB만** | 봇·웹은 systemd `--user`로 관리. journalctl/systemctl이 운영 입문에 직관적 |
| **JSONB config** | 유형마다 스키마가 다름. 유형별 테이블은 추가 때 마이그레이션 비용 |
| **source_key dedup** | health/diet 알림은 rule_id가 없어 CASCADE 불가. source_key upsert로 멱등 재빌드 |

### 알려진 한계

- **음력 윤달·2/30 미지원** — `lunar_to_solar`는 평달만 처리하고, 음력 생일을
  `date(2000, m, d)` 자리표시자로 저장하는 구조라 윤달·2/30을 담을 수 없습니다.
  해당자가 생기면 컬럼 구조(`month`/`day`/`is_leap`) 전환이 필요합니다(audit #45/#53).
- **`birthday_lunar` 센티널 연도** — 음력 생일의 연도(2000)는 의미 없는 자리표시자입니다.
  그래서 나이 필터는 음력만 있는 구성원의 나이를 계산하지 못하고 보수적으로 포함합니다.
- **at-least-once 발송** — exactly-once(SELECT FOR UPDATE / `sending` 상태)를 도입하지
  않았습니다. `_mark_sent`의 조건부 UPDATE·24h staleness 취소·부분 유니크 인덱스로
  중복을 크게 줄이지만, 발송 직후 크래시 등 드문 경우 재발송 가능성이 남습니다.
  가족 규모에서 의식적으로 수용한 트레이드오프입니다.
