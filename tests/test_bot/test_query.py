from datetime import date

from bot.handlers.query import _next_birthday_solar, _parse_days_arg, _preview_message
from shared.config import settings
from shared.models import FamilyMember


def test_parse_days_arg_defaults_to_schedule_horizon():
    assert _parse_days_arg(None) == settings.schedule_horizon_days
    assert _parse_days_arg([]) == settings.schedule_horizon_days
    assert _parse_days_arg(["oops"]) == settings.schedule_horizon_days


def test_parse_days_arg_clamps_range():
    assert _parse_days_arg(["0"]) == 1
    assert _parse_days_arg(["60"]) == 60
    assert _parse_days_arg(["999"]) == 365


def test_preview_message_strips_html_and_shortens():
    message = "<b>홍길동</b>\n" + ("건강검진 " * 20)

    preview = _preview_message(message)

    assert "<b>" not in preview
    assert "\n" not in preview
    assert len(preview) <= 90


def test_next_birthday_solar_basic():
    member = FamilyMember(name="양력", birthday_solar=date(1990, 8, 20))
    assert _next_birthday_solar(member, date(2026, 6, 15)) == date(2026, 8, 20)
    # 이미 지났으면 내년
    assert _next_birthday_solar(member, date(2026, 9, 1)) == date(2027, 8, 20)


def test_next_birthday_solar_feb29_falls_back_in_common_year():
    """양력 2/29 생일 조회가 평년에 ValueError로 크래시하면 안 된다 (audit #24)."""
    member = FamilyMember(name="윤일", birthday_solar=date(1996, 2, 29))
    # 2026은 평년 → 2/28로 폴백
    assert _next_birthday_solar(member, date(2026, 1, 1)) == date(2026, 2, 28)


def test_next_birthday_solar_lunar_only_member():
    """음력 전용 구성원도 다가오는 양력 생일을 계산해야 한다 (audit #54)."""
    member = FamilyMember(name="음력", birthday_lunar=date(2000, 8, 6))
    # 음력 8/6 (2026) → 양력 2026-09-16
    assert _next_birthday_solar(member, date(2026, 6, 15)) == date(2026, 9, 16)


def test_next_birthday_solar_lunar_year_carryover():
    """음력 12월 생일(이듬해 양력 1월)도 연초에 올바르게 잡혀야 한다 (audit #54/#2)."""
    member = FamilyMember(name="음력12", birthday_lunar=date(2000, 12, 15))
    # 음력 2026-12-15 → 양력 2027-01-22
    assert _next_birthday_solar(member, date(2027, 1, 1)) == date(2027, 1, 22)


def test_next_birthday_solar_none_when_unregistered():
    member = FamilyMember(name="미등록")
    assert _next_birthday_solar(member, date(2026, 6, 15)) is None
