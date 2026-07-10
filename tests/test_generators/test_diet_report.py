"""diet_report BMI 리포트 generator 테스트."""

from datetime import UTC, datetime

from shared.generators.diet_report import build_bmi_report
from shared.models import FamilyMember, WeightLog


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
