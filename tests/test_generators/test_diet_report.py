"""diet_report BMI 리포트 generator 테스트."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import shared.generators.diet_report as diet_module
from shared.enums import NotificationStatus
from shared.generators.diet_report import build_bmi_report, rebuild_diet_reports
from shared.models import FamilyMember, ScheduledNotification, WeightLog


def test_bmi_report_escapes_name(db_session) -> None:
    """BMI 리포트에 삽입되는 이름이 escape되어야 한다 (audit #38)."""
    member = FamilyMember(
        name="철수<b> & 영희",
        telegram_user_id=1234,
        height_cm=170,
        diet_active=True,
    )
    db_session.add(member)
    db_session.flush()
    db_session.add(WeightLog(member_id=member.id, weight_kg=65.0, recorded_at=datetime.now(UTC)))
    db_session.flush()

    msg = build_bmi_report(member, db_session)

    assert "철수&lt;b&gt; &amp; 영희" in msg
    assert "철수<b> " not in msg
    # 의도된 마크업(리포트 제목의 <b>)은 유지
    assert "격주 BMI 리포트</b>" in msg


def test_bmi_report_escapes_name_when_no_record(db_session) -> None:
    """기록 없음 메시지 경로도 이름을 escape해야 한다 (audit #38, L122)."""
    member = FamilyMember(
        name="영수<3",
        telegram_user_id=1235,
        height_cm=175,
        diet_active=True,
    )
    db_session.add(member)
    db_session.flush()

    msg = build_bmi_report(member, db_session)

    assert "영수&lt;3" in msg
    assert "영수<3" not in msg


def _pending_diet(db_session, prefix: str = "diet:%") -> list[ScheduledNotification]:
    return (
        db_session.query(ScheduledNotification)
        .filter(
            ScheduledNotification.source_key.like(prefix),
            ScheduledNotification.status == NotificationStatus.pending,
        )
        .all()
    )


def test_rebuild_cancels_stale_when_diet_disabled(db_session) -> None:
    """diet_active를 끄면 이미 예약된 diet:% pending이 취소돼야 한다 (audit #7)."""
    monday = date(2026, 7, 6)  # 월요일
    member = FamilyMember(
        name="다이어터",
        telegram_user_id=9001,
        height_cm=170,
        diet_active=True,
        active=True,
    )
    db_session.add(member)
    db_session.flush()

    rebuild_diet_reports(db_session, horizon_days=30, _today=monday)
    db_session.flush()
    before = _pending_diet(db_session)
    assert before, "rebuild가 diet 알림을 예약해야 한다"
    before_ids = {n.id for n in before}

    # 구성원이 다이어트를 그만둠 → 다음 rebuild에서 묵은 알림이 정리돼야 한다.
    member.diet_active = False
    db_session.flush()

    rebuild_diet_reports(db_session, horizon_days=30, _today=monday)
    db_session.flush()

    assert _pending_diet(db_session) == [], "diet off 후에도 pending 알림이 남음"
    cancelled = (
        db_session.query(ScheduledNotification)
        .filter(
            ScheduledNotification.id.in_(before_ids),
            ScheduledNotification.status == NotificationStatus.cancelled,
        )
        .count()
    )
    assert cancelled == len(before_ids)


def test_rebuild_preserves_desired_notifications(db_session) -> None:
    """stale 취소가 이번 rebuild가 원하는 pending까지 지우면 안 된다 (audit #7 회귀)."""
    monday = date(2026, 7, 6)
    member = FamilyMember(
        name="유지",
        telegram_user_id=9002,
        height_cm=175,
        diet_active=True,
        active=True,
    )
    db_session.add(member)
    db_session.flush()

    rebuild_diet_reports(db_session, horizon_days=30, _today=monday)
    db_session.flush()
    first_ids = {n.id for n in _pending_diet(db_session)}
    assert first_ids

    # 같은 조건으로 다시 rebuild — 동일 desired 집합이므로 아무것도 취소되면 안 된다.
    rebuild_diet_reports(db_session, horizon_days=30, _today=monday)
    db_session.flush()
    still_pending = {n.id for n in _pending_diet(db_session)}
    assert first_ids <= still_pending


def test_rebuild_does_not_resend_sent_nudge_today(db_session, monkeypatch) -> None:
    """당일 09시에 이미 발송된(sent) nudge를 오후 rebuild가 새 pending으로 재생성하지 않는다.

    sent 행은 pending 한정 유니크 인덱스에 없어 upsert가 재삽입하므로, 시각 가드가 없으면
    당일 재시작 배포마다 같은 nudge가 재발송된다 (audit #1)."""
    # 이번 주 월요일 2026-07-06, 오늘은 수요일 2026-07-08
    today = date(2026, 7, 8)
    now = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")).astimezone(UTC)
    monkeypatch.setattr(diet_module, "now_utc", lambda: now)

    member = FamilyMember(
        name="다이어터",
        telegram_user_id=9101,
        height_cm=170,
        diet_active=True,
        active=True,
    )
    db_session.add(member)
    db_session.flush()

    source_key = f"diet:nudge:{member.id}:2026-07-08"
    db_session.add(
        ScheduledNotification(
            source_key=source_key,
            scheduled_at=datetime(2026, 7, 8, 0, 0, tzinfo=UTC),  # 09:00 KST
            target_telegram_id=9101,
            message="이미 발송된 nudge",
            status=NotificationStatus.sent,
        )
    )
    db_session.flush()

    rebuild_diet_reports(db_session, horizon_days=30, _today=today)
    db_session.flush()

    today_slot = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.source_key == source_key)
        .all()
    )
    assert len(today_slot) == 1, "당일 지난 nudge slot이 새 행으로 재삽입됨 (재발송 위험)"
    assert today_slot[0].status == NotificationStatus.sent, "sent 이력이 pending으로 되살아남"


def _bmi_report_dates(db_session, today: date, horizon_days: int = 40) -> set[date]:
    rebuild_diet_reports(db_session, horizon_days=horizon_days, _today=today)
    db_session.flush()
    dates = {
        date.fromisoformat(n.source_key.split(":")[3])
        for n in _pending_diet(db_session, "diet:bmi:%")
    }
    # 다음 anchor 실험을 위해 상태 초기화
    db_session.query(ScheduledNotification).delete()
    db_session.flush()
    return dates


def test_bmi_report_is_biweekly_absolute(db_session) -> None:
    """BMI 리포트가 rebuild 실행 주와 무관하게 격주(절대 패리티)로만 예약돼야 한다 (audit #33)."""
    member = FamilyMember(
        name="비만이",
        telegram_user_id=9003,
        height_cm=170,
        diet_active=True,
        active=True,
    )
    db_session.add(member)
    db_session.flush()

    monday0 = date(2026, 7, 6)  # 월요일
    monday1 = monday0 + timedelta(days=7)  # 다음 주 월요일 (반대 패리티)

    dates0 = _bmi_report_dates(db_session, monday0)
    dates1 = _bmi_report_dates(db_session, monday1)

    tue0 = monday0 + timedelta(days=1)
    tue1 = monday1 + timedelta(days=1)

    # 인접한 두 주는 패리티가 반대이므로, 각자 자기 주 화요일에 BMI가 잡히는지 여부가
    # 정확히 하나만 True여야 한다. 상대 패리티(버그)면 anchor 주가 항상 잡혀 둘 다 True.
    assert (tue0 in dates0) != (tue1 in dates1)

    # 한 rebuild 내에서 BMI 간격은 항상 14일(격주)이어야 한다.
    ordered = sorted(dates0)
    assert len(ordered) >= 2
    gaps = {(b - a).days for a, b in zip(ordered, ordered[1:], strict=False)}
    assert gaps == {14}
