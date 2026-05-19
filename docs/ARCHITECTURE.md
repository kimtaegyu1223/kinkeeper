# KinKeeper 아키텍처

이 문서는 KinKeeper의 설계 결정과 트레이드오프를 설명합니다.  
대상 독자: 코드베이스를 처음 보는 개발자, 또는 "왜 이렇게 만들었지?"라는 질문을 갖고 있는 사람.

---

## 1. 전체 구조

두 개의 독립 프로세스가 하나의 PostgreSQL을 공유합니다.

```
┌──────────────────────────────────┐
│           bot 프로세스             │
│                                  │
│  python-telegram-bot (polling)   │
│  APScheduler (asyncio loop 공유)  │
│  handlers/ — 봇 명령어 처리        │
│  scheduler.py — 알림 발송 루프     │
└────────────────┬─────────────────┘
                 │
         PostgreSQL (Docker)
          shared/models.py
          shared/generators/
                 │
┌────────────────┴─────────────────┐
│           web 프로세스             │
│                                  │
│  FastAPI + Jinja2 + HTMX         │
│  routes/ — 관리자 CRUD            │
│  동기 SQLAlchemy                  │
└──────────────────────────────────┘
```

**왜 두 프로세스로 분리했는가?**

봇은 `asyncio` 이벤트 루프 위에서 동작하고, APScheduler도 같은 루프에 붙어 있습니다. FastAPI도 asyncio 기반이지만, SQLAlchemy를 동기 방식으로 쓰고 있어서 두 프로세스를 합치면 스레드/코루틴 경계가 복잡해집니다. 서로 import하지 않는 규칙을 지키면 각 프로세스를 독립적으로 이해하고 재시작할 수 있습니다.

`shared/`는 두 프로세스가 공통으로 쓰는 코드(모델, DB 세션, 제너레이터)만 포함합니다.

---

## 2. 데이터 모델

### `family_members`

가족 구성원의 마스터 데이터.

| 컬럼 | 설명 | 설계 이유 |
|---|---|---|
| `telegram_user_id` | nullable | 텔레그램 계정 없는 구성원도 등록 가능 (어린이, 노인 등). DM은 못 받지만 그룹 채널 알림은 받음 |
| `gender` | M/F/None | None = 미설정. 건강검진 성별 필터에서 None이면 "해당 없음"이 아니라 "모르니까 보내기"로 처리 |
| `birthday_solar` / `birthday_lunar` | 둘 다 nullable | 양력·음력 중 아는 것만 입력. 두 컬럼이 독립적이어야 생일 알림 생성기가 각각 처리할 수 있음 |
| `height_cm` | nullable | 다이어트 트래킹 활성화 시에만 필요. 없으면 BMI 계산 건너뜀 |
| `diet_active` | boolean | 개인마다 다이어트 트래킹 온/오프 가능 |

### `reminder_rules` + `scheduled_notifications`

알림 규칙과 실제 발송 큐를 분리한 패턴.

```
reminder_rules (규칙 정의)
    │  type: birthday / holiday / custom
    │  config: JSONB (유형별 추가 데이터)
    │  lead_times_days: [7, 3, 0]
    │
    └──► scheduled_notifications (발송 큐)
             scheduled_at, target_telegram_id, message, status
```

**왜 이 패턴인가?**

규칙(rule)은 "무엇을, 언제 보낼지"의 정의입니다. 스케줄러는 규칙을 직접 해석하지 않고 `scheduled_notifications` 테이블만 폴링합니다. 덕분에:

- 규칙이 수정되면 `rebuild_for_rule(rule_id)`로 해당 큐만 재생성. 멱등적으로 동작.
- 스케줄러는 단순히 `status=pending AND scheduled_at <= now` 조건으로 발송. 로직이 분리됨.
- 발송 기록(sent_at, error)이 큐에 남아 추적 가능.

`config`를 JSONB로 저장한 이유: 유형마다 필요한 데이터가 다릅니다(생일은 구성원 ID, 명절은 음력 날짜 등). 별도 테이블로 쪼개면 조인이 늘어나고, 유형 추가 시 마이그레이션이 필요합니다. 가족 규모에서는 JSONB의 유연성이 적합합니다.

### `health_check_types` + `health_check_records` + `member_health_check_configs`

건강검진은 3개 테이블로 구성됩니다.

```
health_check_types (글로벌 검진 항목 정의)
    name, period_years, gender, min_age
    │
    ├──► health_check_records (실제 검진 완료 기록)
    │        member_id, check_type_id, checked_at
    │
    └──► member_health_check_configs (구성원별 오버라이드)
             member_id, check_type_id, period_years, active
```

**왜 rule 기반이 아닌 별도 시스템인가?**

건강검진 알림의 예정일은 "마지막 검진일 + 주기"로 동적으로 계산됩니다. 검진 기록이 추가될 때마다 다음 알림 날짜가 바뀝니다. 규칙 기반 시스템(reminder_rules)은 날짜가 고정된 이벤트에 적합하지만, 건강검진은 상태가 변하는 도메인이라 맞지 않습니다.

**구성원별 오버라이드 (`member_health_check_configs`)가 필요한 이유:**

검진 항목의 기본 주기가 2년이어도, 특정 구성원은 의사 권고로 1년마다 받을 수 있습니다. 또는 특정 항목의 알림 자체를 끄고 싶을 수도 있습니다. `active=False`로 설정하면 해당 구성원에게는 그 항목 알림이 발송되지 않습니다.

우선순위: `config.period_years` > `type.period_years` (config가 None이면 type 기본값 사용)

### `weight_logs`

단순 append-only 로그입니다. 수정/삭제 없이 기록만 쌓습니다. 최신 기록은 `ORDER BY recorded_at DESC LIMIT 1`로 조회하고, BMI 리포트에서 2주 전 기록과 비교합니다.

### `source_key` on `scheduled_notifications`

`rule_id`가 없는 알림(건강검진, 다이어트)의 중복 방지용 키입니다.

```
hc:upcoming:{member_id}:{check_type_id}:{notify_date}
hc:overdue:group:{member_id}:{check_type_id}:{monthly_date}
hc:overdue:dm:{member_id}:{check_type_id}:{nudge_date}
diet:remind:{member_id}:{monday_date}
diet:nudge:{member_id}:{nudge_date}
diet:bmi:{member_id}:{report_date}
```

제너레이터가 `rebuild_*`를 여러 번 호출해도 동일한 `source_key`로 upsert하므로 중복 발송이 없습니다.

---

## 3. 알림 생성 흐름 (Generator 패턴)

```
startup / 매일 새벽 3시
    │
    ├─► rebuild_upcoming()          ← 규칙 기반 제너레이터
    │       birthday_generator.generate(rule, session)
    │       holiday_generator.generate(rule, session)
    │       custom_generator.generate(rule, session)
    │
    ├─► rebuild_health_checks()     ← 상태 기반 제너레이터
    │       DB 전체 순회: member × check_type
    │       due_date 계산 → upsert_notification_by_key
    │
    └─► rebuild_diet_reports()      ← 상태 기반 제너레이터
            diet_active=True 구성원 순회
            weekly remind + nudge + bi-weekly BMI → upsert
```

**규칙 기반 제너레이터 (birthday, holiday, custom)**

`ReminderRule`을 읽어 `lead_times_days`만큼 `ScheduledNotification`을 생성합니다. 규칙이 웹에서 생성/수정되면 `rebuild_for_rule(rule_id)`가 즉시 호출되어 큐를 재빌드합니다. rule 삭제 시 CASCADE로 연관 알림도 삭제됩니다.

**상태 기반 제너레이터 (health_check, diet_report)**

DB의 현재 상태(검진 기록, 몸무게 기록, diet_active 플래그)를 직접 순회하여 알림을 계산합니다. `source_key` 기반 upsert이므로 몇 번을 호출해도 멱등적입니다.

`rebuild_upcoming()`은 봇 프로세스 시작 시 + 매일 새벽 3시에 호출되어 60일치 알림을 항상 최신 상태로 유지합니다.

---

## 4. 스케줄러 (APScheduler)

봇 프로세스 내부에서 같은 asyncio 이벤트 루프를 공유합니다.

```
봇 프로세스 시작
    │
    ├─► APScheduler 시작
    │       interval 1분: poll_and_send()
    │       cron 매일 03:00: rebuild_all()
    │
    └─► python-telegram-bot polling 시작
```

**`poll_and_send()` (1분 간격)**

```sql
SELECT * FROM scheduled_notifications
WHERE scheduled_at <= now()
  AND status = 'pending'
ORDER BY scheduled_at
LIMIT 50
```

조회 후 각 알림을 발송하고 `status=sent`, `sent_at=now()`로 업데이트합니다. 실패 시 `status=error`, `error=메시지`로 기록합니다.

**BMI 리포트 placeholder 처리**

`diet:bmi:*` 알림의 message 값은 `__bmi_report__:{member_id}` 형태의 placeholder입니다. 스케줄러가 발송 직전에 이 값을 감지하면 `build_bmi_report(member, session)`을 호출해 최신 몸무게로 메시지를 생성합니다.

이 방식을 쓰는 이유: BMI 리포트는 "2주 뒤 발송"으로 예약되지만, 실제 메시지는 발송 시점의 최신 몸무게를 반영해야 합니다. 예약 시점에 메시지를 고정하면 2주 전 몸무게가 보고됩니다.

---

## 5. 건강검진 시스템

### 검진 필터링 로직

```python
for member in active_members:
    for ct in active_check_types:
        # 1. 성별 필터
        if ct.gender and member.gender and member.gender != ct.gender:
            continue  # 둘 다 설정됐고 불일치 → 스킵
        # (member.gender가 None이면 "모르니까 발송"으로 처리)

        # 2. 나이 필터
        if ct.min_age is not None:
            if 생일 모름: continue
            if age < ct.min_age: continue

        # 3. 구성원별 config 확인
        if config.active == False: continue
        period = config.period_years or ct.period_years

        # 4. 다음 검진일 계산
        due_date = last_checked + period_years
        if due_date <= today: 미수검 알림
        elif due_date <= horizon: 예정 알림
```

**성별이 None인 구성원**은 성별 필터가 있는 항목도 받습니다. "모른다"는 것은 "해당 없음"이 아닙니다. 가족 봇이라 성별을 입력하지 않은 구성원에게 알림을 빠뜨리는 것이 더 위험합니다.

### 미수검 알림 vs 예정 알림

| 구분 | 조건 | 그룹 채널 | 개인 DM |
|---|---|---|---|
| 예정 | due_date > today, horizon 내 | 30/14/7/당일 | 없음 |
| 미수검 | due_date <= today | 매월 1일 | 매주 (telegram_user_id 있을 때) |

---

## 6. 다이어트 트래킹

### 주간 플로우

```
월요일 09:00  → diet:remind  (몸무게 입력 요청 DM)
화요일 09:00  → diet:nudge   (미입력 시 nudge)
수요일 09:00  → diet:nudge
  ...
일요일 09:00  → diet:nudge

/몸무게 67.2 입력 시
    → WeightLog 저장
    → 이번 주 남은 diet:nudge 알림 status=cancelled
```

nudge를 취소하는 이유: 이미 입력했는데 계속 독촉 메시지가 오면 불편합니다. `weight.py` 핸들러에서 이번 주 월요일~일요일 사이의 `diet:nudge:{member_id}:%` 패턴 알림을 `cancelled`로 일괄 처리합니다.

### 격주 BMI 리포트

```
짝수 주차 화요일 09:00 → diet:bmi
    발송 시점에 build_bmi_report() 호출
    → 최신 몸무게 + 2주 전 비교 + 정상 범위 대비 목표
```

BMI 기준:
- 18.5 미만: 저체중 → 정상 최솟값까지 증량 필요
- 18.5 ~ 22.9: 정상
- 23.0 ~ 24.9: 과체중 → 정상 최댓값까지 감량 필요
- 25.0 이상: 비만 → 감량 필요

정상 체중 범위: `18.5 × h²` ~ `22.9 × h²` (h = 키(m))

---

## 7. 트레이드오프 & 의도적 배제

| 결정 | 이유 |
|---|---|
| **APScheduler, Redis/Celery 없음** | 가족 규모에서 1분 폴링으로 충분. Celery는 브로커, 워커, 모니터링까지 복잡도가 높음 |
| **동기 SQLAlchemy** | async SQLAlchemy는 세션 관리가 더 복잡함. 가족 봇 수준의 쿼리 수에서 동기로도 충분 |
| **polling, webhook 없음** | 서버 공인 IP + HTTPS 설정 없이도 동작. private 가족 봇에 충분 |
| **봇과 웹 분리** | 봇은 asyncio, 웹은 동기 FastAPI. 합치면 to_thread/run_in_executor 처리가 복잡해짐 |
| **Docker는 DB만** | 봇·웹은 systemd로 관리. journalctl로 로그 확인, systemctl로 재시작 — 서버 운영 입문에 더 직관적 |
| **JSONB config** | 알림 유형마다 스키마가 다름. 유형별 테이블 분리는 유형 추가 시 마이그레이션 비용이 있음 |
| **source_key 기반 dedup** | health/diet 알림은 rule_id가 없어 CASCADE 삭제 불가. source_key upsert로 멱등적 재빌드 |
