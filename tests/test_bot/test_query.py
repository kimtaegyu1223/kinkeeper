from bot.handlers.query import _parse_days_arg, _preview_message
from shared.config import settings


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
