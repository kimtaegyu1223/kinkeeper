"""upsert 중복 검사 회귀 테스트 (audit #1, #26).

발송 완료(sent) 행도 중복으로 간주해 rebuild/재시작 시 재삽입·재발송되지 않아야 한다.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from shared.enums import NotificationStatus, ReminderType
from shared.generators.base import upsert_notification, upsert_notification_by_key
from shared.models import ReminderRule, ScheduledNotification


def _make_rule(db_session) -> ReminderRule:
    rule = ReminderRule(
        type=ReminderType.birthday,
        title="생일",
        lead_times_days=[0],
        config={"member_id": 1},
        active=True,
    )
    db_session.add(rule)
    db_session.flush()
    return rule


def test_upsert_notification_skips_when_sent_row_exists(db_session) -> None:
    rule = _make_rule(db_session)
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    # 이미 발송된 동일 slot
    db_session.add(
        ScheduledNotification(
            rule_id=rule.id,
            scheduled_at=scheduled_at,
            target_telegram_id=123,
            message="축하합니다",
            status=NotificationStatus.sent,
        )
    )
    db_session.flush()

    upsert_notification(db_session, rule, scheduled_at, 123, "축하합니다")
    db_session.flush()

    count = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .count()
    )
    assert count == 1, "sent 행이 있는데 새 pending 행이 재삽입됨"


def test_upsert_notification_still_inserts_when_only_cancelled(db_session) -> None:
    """cancelled만 있으면 새 pending을 생성해야 한다 (정상 재활성 경로)."""
    rule = _make_rule(db_session)
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    db_session.add(
        ScheduledNotification(
            rule_id=rule.id,
            scheduled_at=scheduled_at,
            target_telegram_id=123,
            message="축하합니다",
            status=NotificationStatus.cancelled,
        )
    )
    db_session.flush()

    upsert_notification(db_session, rule, scheduled_at, 123, "축하합니다")
    db_session.flush()

    pending = (
        db_session.query(ScheduledNotification)
        .filter(
            ScheduledNotification.rule_id == rule.id,
            ScheduledNotification.status == NotificationStatus.pending,
        )
        .count()
    )
    assert pending == 1


def test_upsert_by_key_skips_when_sent_row_exists(db_session) -> None:
    source_key = "hc:monthly:group:2026-08-01"
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    db_session.add(
        ScheduledNotification(
            rule_id=None,
            source_key=source_key,
            scheduled_at=scheduled_at,
            target_telegram_id=999,
            message="건강검진 리포트",
            status=NotificationStatus.sent,
        )
    )
    db_session.flush()

    upsert_notification_by_key(db_session, source_key, scheduled_at, 999, "건강검진 리포트")
    db_session.flush()

    count = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.source_key == source_key)
        .count()
    )
    assert count == 1, "sent source_key 행이 있는데 새 pending 행이 재삽입됨"


def test_partial_unique_index_blocks_duplicate_pending(db_session) -> None:
    """(rule_id, scheduled_at, target) pending 중복은 DB 레벨에서 차단된다 (audit #29)."""
    rule = _make_rule(db_session)
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    # 실패한 flush가 바깥 트랜잭션을 오염시키지 않도록 SAVEPOINT 안에서 시도한다.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add_all(
            [
                ScheduledNotification(
                    rule_id=rule.id,
                    scheduled_at=scheduled_at,
                    target_telegram_id=123,
                    message="a",
                    status=NotificationStatus.pending,
                ),
                ScheduledNotification(
                    rule_id=rule.id,
                    scheduled_at=scheduled_at,
                    target_telegram_id=123,
                    message="b",
                    status=NotificationStatus.pending,
                ),
            ]
        )
        db_session.flush()


def test_partial_unique_index_allows_pending_plus_sent(db_session) -> None:
    """pending 한정 유니크이므로 동일 slot의 sent 이력과는 공존할 수 있다 (audit #29)."""
    rule = _make_rule(db_session)
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    db_session.add_all(
        [
            ScheduledNotification(
                rule_id=rule.id,
                scheduled_at=scheduled_at,
                target_telegram_id=123,
                message="sent",
                status=NotificationStatus.sent,
            ),
            ScheduledNotification(
                rule_id=rule.id,
                scheduled_at=scheduled_at,
                target_telegram_id=123,
                message="pending",
                status=NotificationStatus.pending,
            ),
        ]
    )
    db_session.flush()  # 예외 없이 통과해야 한다
