# KinKeeper 운영 가이드

운영자(백엔드 초보 1명)와 "몇 달 뒤의 본인"을 위한 실행 가능한 운영 문서입니다.
서비스는 systemd `--user` 유닛 3개로 돌고, DB는 Docker 컨테이너 하나입니다.
설계 배경은 [ARCHITECTURE.md](ARCHITECTURE.md), 기능·명령 개요는
[../README.md](../README.md)를 보세요.

- 유닛: `kinkeeper-bot`, `kinkeeper-web`, `kinkeeper-web-tailscale`
- DB: docker-compose `db` 서비스(`postgres:16-alpine`, `127.0.0.1:5432`)
- 배포/설치/백업 스크립트: `deploy/deploy.sh`, `deploy/install.sh`, `deploy/pg_backup.sh`

아래 명령은 서비스를 실행 중인 유저 셸에서 실행합니다(`systemctl --user`는 로그인
세션 또는 linger가 필요 — 설치 시 `loginctl enable-linger`로 켜 둡니다).

---

## 1. 일상 운영

### 서비스 상태

```bash
# 세 유닛 한 번에
systemctl --user status kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale --no-pager

# 활성 여부만
systemctl --user is-active kinkeeper-bot kinkeeper-web
```

### 로그

```bash
# 실시간
journalctl --user -u kinkeeper-bot -f
journalctl --user -u kinkeeper-web -f

# 에러만, 최근 1시간
journalctl --user -u kinkeeper-bot -p err --since "1 hour ago"
```

로그는 structlog JSON입니다. 특정 이벤트를 찾을 때:

```bash
journalctl --user -u kinkeeper-bot --since today | grep '"발송 실패"'
journalctl --user -u kinkeeper-bot --since today | grep '"알림 예정 재생성 완료"'
```

### 재시작

```bash
# 코드/설정(.env) 반영은 재시작으로
systemctl --user restart kinkeeper-bot kinkeeper-web kinkeeper-web-tailscale
```

> `.env`를 바꿨으면 반드시 재시작해야 반영됩니다(EnvironmentFile은 시작 시 1회 읽음).
> `MEMORY.md` 운영 메모대로, 코드 반영도 `systemctl --user restart`입니다.

### 헬스체크 (`/healthz`)

관리자 웹이 DB 연결을 확인합니다.

```bash
curl -i http://127.0.0.1:8000/healthz
# DB 정상: 200 {"status":"ok","db":"ok"}
# DB 장애: 503 {"status":"db_error","db":"error"}
```

#### cron 경보 (`deploy/healthz_alert.sh`)

`/healthz` 실패(웹 다운으로 연결 거부 또는 DB 장애로 503)를 감지하면 텔레그램
그룹으로 경보를 보냅니다. 연속 장애 스팸을 막기 위해 **같은 장애당 1시간에 1회**만
경보하고(상태파일 쿨다운), 정상으로 돌아오면 **'복구됨' 1회**를 보냅니다. `.env`의
`TELEGRAM_BOT_TOKEN`/`GROUP_CHAT_ID`를 사용합니다.

```bash
# 수동 1회 (동작 확인)
bash deploy/healthz_alert.sh

# 5분마다 자동 (crontab -e)
*/5 * * * * bash /home/ktg/projects/kinkeeper/deploy/healthz_alert.sh
```

> 상태파일은 `${XDG_STATE_HOME:-~/.local/state}/kinkeeper/healthz_alert.state`에
> 저장됩니다. 경보가 반복되면 지우지 말고 원인(웹/DB)을 먼저 확인하세요.

---

## 2. 배포 절차

일반 배포는 스크립트 한 방입니다:

```bash
bash deploy/deploy.sh
```

`deploy/deploy.sh`는 `git pull origin main` → `uv sync` →
`uv run alembic upgrade head` → 유닛 3개 `systemctl --user restart` →
`systemctl --user status`를 순서대로 실행합니다.

### ⚠️ 이 브랜치의 미적용 마이그레이션 2건

`chore/overhaul` 브랜치에는 아직 라이브 DB에 **적용되지 않은 마이그레이션 2건**이
있습니다. 이 브랜치를 배포(머지)할 때 `alembic upgrade head`가 이 둘을 올립니다.

| revision | 내용 | 주의 |
|---|---|---|
| `a1b2c3d4e5f6` | `scheduled_notifications` pending 한정 **부분 유니크 인덱스 2개** 생성 | **적용 전 기존 중복 pending 행을 정리해야** 인덱스 생성이 성공 |
| `c7f3a9e21b04` | `family_members.timezone` 컬럼 삭제(write-only, 미사용) | 특별한 사전 조치 없음 |

체인 순서: `... → 699ca1657399 → a1b2c3d4e5f6 → c7f3a9e21b04(head)`.

#### 배포 전 체크리스트

1. **먼저 백업**(§3) — `bash deploy/pg_backup.sh`
2. **현재 적용 리비전 확인**

   ```bash
   uv run alembic current
   uv run alembic history   # head까지 남은 마이그레이션 확인
   ```

3. **중복 pending 사전 점검**(`a1b2c3d4e5f6`가 실패하지 않도록). db 컨테이너 이름은
   `docker compose ps -q db`로 찾습니다.

   ```bash
   CID=$(docker compose ps -q db)

   # (a) rule 인덱스 대상 중복: (rule_id, scheduled_at, target_telegram_id)
   docker exec -i "$CID" psql -U family -d family_notifier <<'SQL'
   SELECT rule_id, scheduled_at, target_telegram_id, count(*)
   FROM scheduled_notifications
   WHERE status = 'pending' AND rule_id IS NOT NULL
   GROUP BY rule_id, scheduled_at, target_telegram_id
   HAVING count(*) > 1;
   SQL

   # (b) source_key 인덱스 대상 중복
   docker exec -i "$CID" psql -U family -d family_notifier <<'SQL'
   SELECT source_key, count(*)
   FROM scheduled_notifications
   WHERE status = 'pending' AND source_key IS NOT NULL
   GROUP BY source_key
   HAVING count(*) > 1;
   SQL
   ```

   두 쿼리가 **0행이면 그대로 배포**해도 됩니다. 행이 나오면, 각 중복 그룹에서
   가장 낮은 id만 남기고 나머지를 취소한 뒤 다시 점검합니다:

   ```bash
   # 중복 pending 정리 — 그룹별 최소 id만 pending 유지, 나머지는 cancelled
   docker exec -i "$CID" psql -U family -d family_notifier <<'SQL'
   -- (a) rule 그룹
   UPDATE scheduled_notifications s SET status = 'cancelled'
   WHERE s.status = 'pending' AND s.rule_id IS NOT NULL
     AND s.id > (
       SELECT min(t.id) FROM scheduled_notifications t
       WHERE t.status = 'pending' AND t.rule_id IS NOT NULL
         AND t.rule_id = s.rule_id
         AND t.scheduled_at = s.scheduled_at
         AND t.target_telegram_id = s.target_telegram_id
     );
   -- (b) source_key 그룹
   UPDATE scheduled_notifications s SET status = 'cancelled'
   WHERE s.status = 'pending' AND s.source_key IS NOT NULL
     AND s.id > (
       SELECT min(t.id) FROM scheduled_notifications t
       WHERE t.status = 'pending' AND t.source_key IS NOT NULL
         AND t.source_key = s.source_key
     );
   SQL
   ```

   > 취소된 행은 발송되지 않고, 다음 03시 rebuild가 필요한 pending을 다시 만듭니다.
   > 중복은 대부분 마이그레이션 이전 코드가 남긴 잔재이므로 취소해도 안전합니다.

4. **배포 실행** — `bash deploy/deploy.sh`
5. **확인** — `uv run alembic current`가 `c7f3a9e21b04`인지, 유닛 3개가 active인지,
   `journalctl`에 시작 시 rebuild 로그가 찍히는지 확인.

---

## 3. 백업/복구

### 백업 (`deploy/pg_backup.sh`)

db 컨테이너를 `docker compose ps -q db`로 찾아 `pg_dump | gzip`으로
`~/backups/kinkeeper/{DB}_{타임스탬프}.sql.gz`에 저장하고, 30일 지난 백업을 삭제합니다.
중간 실패 시 잘린 파일이 정상 백업으로 오인되지 않도록 임시 파일에 먼저 쓰고 성공 시에만
최종 이름으로 옮깁니다.

```bash
# 수동 1회
bash deploy/pg_backup.sh

# 매일 새벽 2시 자동 (crontab -e)
0 2 * * * bash /home/ktg/projects/kinkeeper/deploy/pg_backup.sh
```

### 복구

```bash
CID=$(docker compose ps -q db)

# gzip 백업을 psql로 주입 (POSTGRES_* 자격증명 사용)
gunzip -c ~/backups/kinkeeper/family_notifier_YYYYMMDD_HHMMSS.sql.gz \
  | docker exec -i "$CID" psql -U family -d family_notifier
```

> 깨끗한 복구가 필요하면 복원 전에 앱 유닛을 멈추고(`systemctl --user stop
> kinkeeper-bot kinkeeper-web`) DB를 비운 뒤 주입하세요. `pg_dump` 기본 출력은
> `CREATE TABLE`을 포함하므로 빈 DB에 넣는 것이 가장 깔끔합니다. 복원 후 유닛을
> 다시 시작합니다.

---

## 4. 흔한 장애와 대응

### 텔레그램 발송 실패

`send_message`(`shared/notifier.py`)는 타임아웃·네트워크 순단·5xx·429를 자동
재시도합니다(최대 3회, 429는 Retry-After 존중, §ARCHITECTURE 4). 재시도가 소진되거나
영구 4xx(400 chat not found·403 봇 차단)면 해당 알림이 `failed`가 되고 사유가
`error` 컬럼에 남습니다.

```bash
CID=$(docker compose ps -q db)
docker exec -i "$CID" psql -U family -d family_notifier <<'SQL'
SELECT id, target_telegram_id, scheduled_at, error
FROM scheduled_notifications
WHERE status = 'failed'
ORDER BY scheduled_at DESC
LIMIT 20;
SQL
```

- **403/봇 차단·400 chat not found**: 대상이 봇을 차단했거나 `GROUP_CHAT_ID`가 잘못됨.
  구성원/그룹 설정을 확인.
- **429 반복**: 발송량 과다. 가족 규모에선 드묾. 로그의 `retry_after`를 확인.
- 재시도로 자연 회복되는 일시 오류는 굳이 손댈 필요 없습니다.

### DB 미기동 / 연결 실패

- **봇**: 시작 시 rebuild가 DB 미기동이면 지수 백오프로 몇 회 재시도하고, 끝내 실패해도
  **크래시하지 않고 폴링을 계속**합니다(03시 cron이 rebuild를 보충). 즉 봇이 죽지는
  않지만 예약 알림이 안 채워질 수 있습니다.
- **웹**: `/healthz`가 503. `check_db_connection`이 `SELECT 1`에 실패.
- **조치**: `docker compose ps`로 db 상태 확인 → `docker compose up -d db` →
  `docker compose exec db pg_isready -U family -d family_notifier`로 준비 확인 →
  필요 시 유닛 재시작.

### 03시 rebuild가 안 돈 것 같을 때

정상이면 매일 03시(KST)에 `"알림 예정 재생성 완료"` 로그가 찍힙니다.

```bash
journalctl --user -u kinkeeper-bot --since "03:00" --until "03:10" | grep 재생성
```

- 로그가 없으면: 03시에 봇이 떠 있었는지(`journalctl`로 재시작/크래시 흔적),
  DB가 살아 있었는지 확인. cron 잡은 `misfire_grace_time=3600`이라 최대 1시간 지연은
  회복하지만, 그 이상 지연·프로세스 부재면 그 회차가 누락됩니다.
- 봇 재시작 시 `_startup_rebuild`가 즉시 1회 rebuild하므로, 수동 회복이 필요하면
  `systemctl --user restart kinkeeper-bot`로 재빌드를 유도할 수 있습니다.

---

## 미결 사항

주인(운영자 본인) 결정이 필요한 항목. 각 한 줄, **결정 대기**.

- **다이어트/몸무게 기능의 운명** — **결정: 폐기(2026-07-11).** 관련 코드·스키마(weight_logs 테이블, `family_members.height_cm`/`diet_active`, `remindertype.diet_report`, `WEIGHT_FEATURE_ENABLED`)를 전량 제거. 마이그레이션 `b8e4f1a7c2d9`로 스키마 드롭(운영 DB에 diet_report 규칙 0건 확인 후 적용).
- **`SCHEDULE_HORIZON_DAYS` 값** — **결정: 90일(2026-07-11).** 코드 기본값·`.env.example`을 90으로 통일. 운영 `.env`는 배포 때 별도 반영.
- **음력 윤달·2/30 스키마** — **결정: 해당 가족 없음 확인, 센티널 유지(2026-07-11).** 윤달/음력 2·30 해당자가 생기면 `(월, 일, 윤달)` 3컬럼 전환 필요(현재는 입력 단계에서 거부). 배경은 [ARCHITECTURE 알려진 한계](ARCHITECTURE.md#알려진-한계) 참조.
- **`/healthz` 모니터 연결** — **결정: cron 경보(2026-07-11).** 외부 uptime 모니터 대신 `deploy/healthz_alert.sh`를 5분 crontab에 등록해 텔레그램으로 경보(§1 헬스체크 참조).
