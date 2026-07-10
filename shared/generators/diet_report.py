"""다이어트/몸무게 알림 generator.

diet_active=True인 구성원에게:
- 매주 월요일 9시: 몸무게 입력 DM
- 화~일: 그 주 기록 없으면 매일 DM (nudge)
- 격주 화요일: BMI 리포트 DM (월요일 기록을 반영하려 하루 늦춰 발송)
"""

from datetime import UTC, date, datetime, timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.enums import NotificationStatus
from shared.generators._time import now_utc, scheduled_at_local, today_local
from shared.generators.base import upsert_notification_by_key
from shared.models import FamilyMember, ScheduledNotification, WeightLog

# 격주 BMI 리포트 패리티 기준 월요일(고정 epoch). 리빌드 실행 주와 무관하게
# 절대 주차로 짝/홀을 판정하기 위한 앵커다 (audit #33). 1970-01-05는 월요일.
_BIWEEKLY_EPOCH = date(1970, 1, 5)


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _bmi(weight_kg: float, height_cm: int) -> float:
    h = height_cm / 100
    return weight_kg / (h * h)


def _normal_weight_range(height_cm: int) -> tuple[float, float]:
    h = height_cm / 100
    return (18.5 * h * h, 22.9 * h * h)


def rebuild_diet_reports(
    session: Session, horizon_days: int = 60, _today: date | None = None
) -> None:
    today = _today or today_local()
    horizon = today + timedelta(days=horizon_days)

    members = session.scalars(
        select(FamilyMember).where(
            FamilyMember.active.is_(True),
            FamilyMember.diet_active.is_(True),
            FamilyMember.telegram_user_id.isnot(None),
        )
    ).all()

    now = now_utc()
    desired_source_keys: set[str] = set()
    for member in members:
        if not member.telegram_user_id or not member.height_cm:
            continue
        _schedule_member(session, member, today, horizon, now, desired_source_keys)

    # 이번 rebuild가 원하지 않는 diet:% pending을 취소한다. diet_active/active off,
    # height 제거 등으로 대상에서 빠진 구성원의 묵은 알림이 계속 발송되는 것을 막는다
    # (audit #7, health_check의 _cancel_stale_health_notifications와 동일 패턴).
    _cancel_stale_diet_notifications(session, desired_source_keys)


def _cancel_stale_diet_notifications(session: Session, desired_source_keys: set[str]) -> None:
    rows = session.scalars(
        select(ScheduledNotification).where(
            ScheduledNotification.source_key.like("diet:%"),
            ScheduledNotification.status == NotificationStatus.pending,
        )
    ).all()
    for row in rows:
        if row.source_key not in desired_source_keys:
            row.status = NotificationStatus.cancelled


def _schedule_member(
    session: Session,
    member: FamilyMember,
    today: date,
    horizon: date,
    now: datetime,
    desired_source_keys: set[str],
) -> None:
    assert member.telegram_user_id is not None
    assert member.height_cm is not None

    # 이번 주 월요일부터 시작
    monday = _monday_of_week(today)

    while monday <= horizon:
        # 매주 월요일: 몸무게 입력 알림
        if monday >= today:
            scheduled_at = scheduled_at_local(monday)
            # 오늘이지만 이미 지난 시각의 slot은 재생성하지 않는다. sent 행은 pending
            # 한정 유니크 인덱스에 없어 upsert가 새 pending을 INSERT하므로, 당일 재시작
            # 재발송을 막으려면 rule 생성기와 동일한 시각 가드가 필요하다 (audit #1).
            if not (monday == today and scheduled_at < now):
                source_key = f"diet:remind:{member.id}:{monday.isoformat()}"
                desired_source_keys.add(source_key)
                msg = "⚖️ 이번 주 몸무게를 입력해주세요!\n→ <code>/몸무게 XX.X</code>"
                upsert_notification_by_key(
                    session, source_key, scheduled_at, member.telegram_user_id, msg
                )

        # 화~일: 매일 nudge (입력하면 bot handler에서 취소)
        for day_offset in range(1, 7):
            nudge_date = monday + timedelta(days=day_offset)
            if nudge_date < today or nudge_date > horizon:
                continue
            scheduled_at = scheduled_at_local(nudge_date)
            # 오늘이지만 이미 지난 시각의 slot은 재생성하지 않는다 (audit #1).
            if nudge_date == today and scheduled_at < now:
                continue
            source_key = f"diet:nudge:{member.id}:{nudge_date.isoformat()}"
            desired_source_keys.add(source_key)
            msg = "⚖️ 아직 이번 주 몸무게를 입력하지 않았어요!\n→ <code>/몸무게 XX.X</code>"
            upsert_notification_by_key(
                session, source_key, scheduled_at, member.telegram_user_id, msg
            )

        # 격주 월요일: BMI 리포트. 패리티를 고정 epoch 기준 절대 주차로 판정해
        # 리빌드 실행 주와 무관하게 항상 같은 주에만 발송한다 (audit #33).
        absolute_week = (monday - _BIWEEKLY_EPOCH).days // 7
        if absolute_week % 2 == 0 and monday >= today:
            report_date = monday + timedelta(days=1)  # 화요일에 발송 (월요일 기록 반영)
            if report_date <= horizon:
                scheduled_at = scheduled_at_local(report_date)
                source_key = f"diet:bmi:{member.id}:{report_date.isoformat()}"
                desired_source_keys.add(source_key)
                # 메시지는 실제 발송 시점에 동적으로 생성해야 하므로 placeholder
                msg = f"__bmi_report__:{member.id}"
                upsert_notification_by_key(
                    session, source_key, scheduled_at, member.telegram_user_id, msg
                )

        monday += timedelta(weeks=1)


def build_bmi_report(member: FamilyMember, session: Session) -> str:
    """실제 발송 시점에 최신 몸무게로 BMI 리포트 생성."""
    assert member.height_cm is not None

    # 이름은 임의 입력이므로 HTML 특수문자를 escape (parse_mode=HTML 발송)
    member_name = escape(member.name)

    latest = session.scalar(
        select(WeightLog)
        .where(WeightLog.member_id == member.id)
        .order_by(WeightLog.recorded_at.desc())
        .limit(1)
    )
    if not latest:
        return (
            f"📊 {member_name}님 BMI 리포트\n몸무게 기록이 없습니다. /몸무게 XX.X 로 입력해주세요!"
        )

    bmi = _bmi(float(latest.weight_kg), member.height_cm)
    low, high = _normal_weight_range(member.height_cm)
    current = float(latest.weight_kg)

    if bmi < 18.5:
        status = "저체중"
        diff_msg = f"정상 범위까지 <b>{low - current:.1f}kg</b> 증량 필요"
    elif bmi <= 22.9:
        status = "정상"
        diff_msg = "정상 체중 범위입니다 👍"
    elif bmi <= 24.9:
        status = "과체중"
        diff_msg = f"정상 범위까지 <b>{current - high:.1f}kg</b> 감량 필요"
    else:
        status = "비만"
        diff_msg = f"정상 범위까지 <b>{current - high:.1f}kg</b> 감량 필요"

    # 2주 전 기록과 비교
    two_weeks_ago = datetime.now(UTC) - timedelta(weeks=2)
    prev = session.scalar(
        select(WeightLog)
        .where(
            WeightLog.member_id == member.id,
            WeightLog.recorded_at < two_weeks_ago,
        )
        .order_by(WeightLog.recorded_at.desc())
        .limit(1)
    )
    trend = ""
    if prev:
        delta = current - float(prev.weight_kg)
        if delta > 0:
            trend = f"\n2주 전 대비 <b>+{delta:.1f}kg</b> ▲"
        elif delta < 0:
            trend = f"\n2주 전 대비 <b>{delta:.1f}kg</b> ▼"
        else:
            trend = "\n2주 전과 동일"

    return (
        f"📊 <b>{member_name}님 격주 BMI 리포트</b>\n"
        f"몸무게: <b>{current:.1f}kg</b> | BMI: <b>{bmi:.1f}</b> ({status}){trend}\n"
        f"정상 범위: {low:.1f}~{high:.1f}kg\n"
        f"{diff_msg}"
    )
