"""bot/scheduler dispatch 회귀 테스트 (audit #27, #28, #30, #56).

scheduler는 shared.db.get_session(전역 엔진)을 쓰므로, 테스트 컨테이너 엔진에
바인딩한 세션 팩토리로 monkeypatch한다. 각 테스트 후 테이블을 비운다.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import bot.scheduler as sched
from shared.enums import NotificationStatus
from shared.models import ScheduledNotification


@pytest.fixture
def scheduler_db(db_engine, monkeypatch):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(sched, "get_session", _get_session)
    yield Session
    # cleanup
    with _get_session() as s:
        s.query(ScheduledNotification).delete()


def _add(Session, **kwargs) -> int:
    defaults = dict(
        rule_id=None,
        source_key=None,
        target_telegram_id=111,
        message="알림",
        status=NotificationStatus.pending,
    )
    defaults.update(kwargs)
    with Session() as s:
        n = ScheduledNotification(**defaults)
        s.add(n)
        s.commit()
        return n.id


def _status(Session, nid: int) -> NotificationStatus:
    with Session() as s:
        row = s.get(ScheduledNotification, nid)
        assert row is not None
        return row.status


# ---------------------------------------------------------------------------
# #27 _mark_sent는 pending일 때만 갱신
# ---------------------------------------------------------------------------


def test_mark_sent_updates_pending(scheduler_db) -> None:
    nid = _add(scheduler_db, scheduled_at=datetime(2026, 1, 1, tzinfo=UTC))
    sched._mark_sent(nid, True)
    assert _status(scheduler_db, nid) == NotificationStatus.sent


def test_mark_sent_ignores_cancelled(scheduler_db) -> None:
    """fetch~발송 사이 취소된 행을 sent로 덮어쓰면 안 된다 (audit #27)."""
    nid = _add(
        scheduler_db,
        scheduled_at=datetime(2026, 1, 1, tzinfo=UTC),
        status=NotificationStatus.cancelled,
    )
    sched._mark_sent(nid, True)
    assert _status(scheduler_db, nid) == NotificationStatus.cancelled


# ---------------------------------------------------------------------------
# #56 오래 지난 알림은 발송하지 않고 취소
# ---------------------------------------------------------------------------


async def test_dispatch_cancels_stale_notifications(scheduler_db, monkeypatch) -> None:
    now = datetime.now(UTC)
    stale_id = _add(scheduler_db, scheduled_at=now - timedelta(hours=48))
    fresh_id = _add(scheduler_db, scheduled_at=now - timedelta(minutes=1))

    sent: list[int] = []

    async def fake_send(chat_id, message):
        sent.append(chat_id)
        return True

    monkeypatch.setattr(sched, "send_message", fake_send)

    await sched.dispatch_pending()

    assert _status(scheduler_db, stale_id) == NotificationStatus.cancelled
    assert _status(scheduler_db, fresh_id) == NotificationStatus.sent
    assert len(sent) == 1, "stale 알림까지 발송됨"


# ---------------------------------------------------------------------------
# #28 한 행 처리 실패가 배치 전체를 중단시키지 않음
# ---------------------------------------------------------------------------


async def test_dispatch_isolates_row_failure(scheduler_db, monkeypatch) -> None:
    now = datetime.now(UTC)
    _add(scheduler_db, scheduled_at=now - timedelta(minutes=2))
    _add(scheduler_db, scheduled_at=now - timedelta(minutes=1))

    sent: list[int] = []

    async def fake_send(chat_id, message):
        sent.append(chat_id)
        return True

    monkeypatch.setattr(sched, "send_message", fake_send)

    calls = {"n": 0}
    real_mark = sched._mark_sent

    def flaky_mark(nid, success, error=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("DB 순단")
        return real_mark(nid, success, error)

    monkeypatch.setattr(sched, "_mark_sent", flaky_mark)

    await sched.dispatch_pending()

    # 첫 행의 mark 실패에도 두 행 모두 발송이 시도되어야 한다.
    assert len(sent) == 2


# ---------------------------------------------------------------------------
# #30 보존기간 지난 종료 상태 행 정리
# ---------------------------------------------------------------------------


def test_purge_old_notifications(scheduler_db) -> None:
    now = datetime.now(UTC)
    old_sent = _add(
        scheduler_db,
        scheduled_at=now - timedelta(days=100),
        status=NotificationStatus.sent,
    )
    old_pending = _add(
        scheduler_db,
        scheduled_at=now - timedelta(days=100),
        status=NotificationStatus.pending,
    )
    recent_cancelled = _add(
        scheduler_db,
        scheduled_at=now - timedelta(days=10),
        status=NotificationStatus.cancelled,
    )

    with scheduler_db() as s:
        purged = sched._purge_old_notifications(s)
        s.commit()

    assert purged == 1
    with scheduler_db() as s:
        assert s.get(ScheduledNotification, old_sent) is None
        assert s.get(ScheduledNotification, old_pending) is not None  # pending은 보존
        assert s.get(ScheduledNotification, recent_cancelled) is not None  # 90일 이내 보존
