from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from shared.enums import NotificationStatus
from shared.models import ReminderRule, ScheduledNotification

# 부분 유니크 인덱스(models.py)의 술어와 정확히 일치해야 ON CONFLICT가 해당
# 인덱스를 대상으로 삼는다.
_RULE_INDEX_WHERE = text("status = 'pending' AND rule_id IS NOT NULL")
_SOURCE_INDEX_WHERE = text("status = 'pending' AND source_key IS NOT NULL")


def upsert_notification(
    session: Session,
    rule: ReminderRule,
    scheduled_at: datetime,
    target_telegram_id: int,
    message: str,
) -> None:
    """같은 rule_id + scheduled_at + target의 pending이 없을 때만 insert.

    중복 판정을 앱단 SELECT 대신 부분 유니크 인덱스(uq_sched_notif_rule_pending)에
    위임한다. pending 중복은 ON CONFLICT DO NOTHING으로 무시된다. 이미 발송된 slot의
    재삽입은 각 생성기가 과거 날짜/시각 slot을 건너뛰는 것으로 막는다 (audit #1).
    """
    stmt = pg_insert(ScheduledNotification).values(
        rule_id=rule.id,
        scheduled_at=scheduled_at,
        target_telegram_id=target_telegram_id,
        message=message,
        status=NotificationStatus.pending,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["rule_id", "scheduled_at", "target_telegram_id"],
        index_where=_RULE_INDEX_WHERE,
    )
    session.execute(stmt)


def upsert_notification_by_key(
    session: Session,
    source_key: str,
    scheduled_at: datetime,
    target_telegram_id: int,
    message: str,
) -> None:
    """source_key 기반 알림을 insert하거나 기존 pending을 in-place로 갱신한다.

    건강검진 리포트처럼 같은 source_key라도 리빌드마다 내용(대상 항목)이 달라질 수
    있으므로, pending이 이미 있으면 scheduled_at/target/message를 갱신한다. 중복 판정과
    갱신은 부분 유니크 인덱스(uq_sched_notif_source_pending)에 위임하며, 이 하나의
    upsert 규칙을 rule 없는 모든 알림(건강검진·다이어트)이 공유한다 (audit #26).
    """
    stmt = pg_insert(ScheduledNotification).values(
        rule_id=None,
        source_key=source_key,
        scheduled_at=scheduled_at,
        target_telegram_id=target_telegram_id,
        message=message,
        status=NotificationStatus.pending,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_key"],
        index_where=_SOURCE_INDEX_WHERE,
        set_={
            "scheduled_at": stmt.excluded.scheduled_at,
            "target_telegram_id": stmt.excluded.target_telegram_id,
            "message": stmt.excluded.message,
        },
    )
    session.execute(stmt)
