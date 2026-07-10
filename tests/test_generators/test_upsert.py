"""upsert 중복 처리 회귀 테스트 (audit #1, #26, #29).

중복 판정을 부분 유니크 인덱스(pending 한정)에 위임한다:
- upsert_notification: 같은 pending slot 중복은 ON CONFLICT DO NOTHING.
- upsert_notification_by_key: 같은 source_key pending은 in-place로 갱신(update).
이미 발송된(sent) slot의 재삽입·재발송은 각 생성기가 과거 날짜/시각 slot을
건너뛰는 것으로 막는다(test_birthday_skips_past_slot_today 참조).
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


def test_upsert_notification_skips_when_pending_row_exists(db_session) -> None:
    """같은 slot의 pending이 이미 있으면 ON CONFLICT DO NOTHING으로 중복 insert되지 않는다."""
    rule = _make_rule(db_session)
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    db_session.add(
        ScheduledNotification(
            rule_id=rule.id,
            scheduled_at=scheduled_at,
            target_telegram_id=123,
            message="축하합니다",
            status=NotificationStatus.pending,
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
    assert count == 1, "pending 중복이 재삽입됨"


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


def test_upsert_by_key_updates_pending_in_place(db_session) -> None:
    """같은 source_key의 pending이 있으면 새 행 대신 기존 pending을 in-place로 갱신한다.

    건강검진 월간 리포트처럼 리빌드마다 대상 항목이 달라지는 알림의 내용 최신화를
    보장한다(health_check가 쓰던 update-in-place 규칙을 base로 통일).
    """
    source_key = "hc:monthly:group:2026-08-01"
    scheduled_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    db_session.add(
        ScheduledNotification(
            rule_id=None,
            source_key=source_key,
            scheduled_at=scheduled_at,
            target_telegram_id=999,
            message="옛 리포트",
            status=NotificationStatus.pending,
        )
    )
    db_session.flush()

    upsert_notification_by_key(db_session, source_key, scheduled_at, 999, "새 리포트")
    db_session.flush()
    db_session.expire_all()

    rows = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.source_key == source_key)
        .all()
    )
    assert len(rows) == 1, "source_key 중복이 재삽입됨"
    assert rows[0].message == "새 리포트", "기존 pending이 새 내용으로 갱신되지 않음"


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
