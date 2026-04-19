"""다이어트/몸무게 알림 generator.

diet_active=True인 구성원에게:
- 매주 월요일 9시: 몸무게 입력 DM
- 화~일: 그 주 기록 없으면 매일 DM (nudge)
- 격주 월요일: BMI 리포트 DM
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.generators.base import upsert_notification_by_key
from shared.models import FamilyMember, WeightLog


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
    today = _today or datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)

    members = session.scalars(
        select(FamilyMember).where(
            FamilyMember.active.is_(True),
            FamilyMember.diet_active.is_(True),
            FamilyMember.telegram_user_id.isnot(None),
        )
    ).all()

    for member in members:
        if not member.telegram_user_id or not member.height_cm:
            continue
        _schedule_member(session, member, today, horizon)


def _schedule_member(session: Session, member: FamilyMember, today: date, horizon: date) -> None:
    assert member.telegram_user_id is not None
    assert member.height_cm is not None

    # 이번 주 월요일부터 시작
    monday = _monday_of_week(today)
    week_num = 0

    while monday <= horizon:
        # 매주 월요일: 몸무게 입력 알림
        if monday >= today:
            scheduled_at = datetime(monday.year, monday.month, monday.day, 9, 0, tzinfo=UTC)
            source_key = f"diet:remind:{member.id}:{monday.isoformat()}"
            msg = "⚖️ 이번 주 몸무게를 입력해주세요!\n→ <code>/몸무게 XX.X</code>"
            upsert_notification_by_key(
                session, source_key, scheduled_at, member.telegram_user_id, msg
            )

        # 화~일: 매일 nudge (입력하면 bot handler에서 취소)
        for day_offset in range(1, 7):
            nudge_date = monday + timedelta(days=day_offset)
            if nudge_date < today or nudge_date > horizon:
                continue
            scheduled_at = datetime(
                nudge_date.year, nudge_date.month, nudge_date.day, 9, 0, tzinfo=UTC
            )
            source_key = f"diet:nudge:{member.id}:{nudge_date.isoformat()}"
            msg = "⚖️ 아직 이번 주 몸무게를 입력하지 않았어요!\n→ <code>/몸무게 XX.X</code>"
            upsert_notification_by_key(
                session, source_key, scheduled_at, member.telegram_user_id, msg
            )

        # 격주 월요일: BMI 리포트 (week_num 짝수 주)
        if week_num % 2 == 0 and monday >= today:
            report_date = monday + timedelta(days=1)  # 화요일에 발송 (월요일 기록 반영)
            if report_date <= horizon:
                scheduled_at = datetime(
                    report_date.year, report_date.month, report_date.day, 9, 0, tzinfo=UTC
                )
                source_key = f"diet:bmi:{member.id}:{report_date.isoformat()}"
                # 메시지는 실제 발송 시점에 동적으로 생성해야 하므로 placeholder
                msg = f"__bmi_report__:{member.id}"
                upsert_notification_by_key(
                    session, source_key, scheduled_at, member.telegram_user_id, msg
                )

        monday += timedelta(weeks=1)
        week_num += 1


def build_bmi_report(member: FamilyMember, session: Session) -> str:
    """실제 발송 시점에 최신 몸무게로 BMI 리포트 생성."""
    assert member.height_cm is not None

    latest = session.scalar(
        select(WeightLog)
        .where(WeightLog.member_id == member.id)
        .order_by(WeightLog.recorded_at.desc())
        .limit(1)
    )
    if not latest:
        return (
            f"📊 {member.name}님 BMI 리포트\n몸무게 기록이 없습니다. /몸무게 XX.X 로 입력해주세요!"
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
        f"📊 <b>{member.name}님 격주 BMI 리포트</b>\n"
        f"몸무게: <b>{current:.1f}kg</b> | BMI: <b>{bmi:.1f}</b> ({status}){trend}\n"
        f"정상 범위: {low:.1f}~{high:.1f}kg\n"
        f"{diff_msg}"
    )
