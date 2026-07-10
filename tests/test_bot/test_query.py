from datetime import date

from bot.handlers.query import (
    _chunk_lines,
    _next_birthday_solar,
    _parse_days_arg,
    _preview_message,
    _utf16_len,
)
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


def test_chunk_lines_splits_over_telegram_limit():
    """4096자를 넘는 목록은 줄 단위로 나뉘고 각 조각은 한도 이내여야 한다 (audit #48)."""
    lines = ["• 06/15 09:00 - " + ("건강검진 " * 10) for _ in range(200)]

    chunks = _chunk_lines(lines, limit=1000)

    assert len(chunks) > 1
    for chunk in chunks:
        assert _utf16_len(chunk) <= 1000
    # 모든 줄이 보존되어야 한다.
    assert sum(c.count("• 06/15") for c in chunks) == 200


def test_chunk_lines_single_chunk_when_short():
    lines = ["헤더", "• 항목1", "• 항목2"]
    assert _chunk_lines(lines) == ["헤더\n• 항목1\n• 항목2"]


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str | None]] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append((text, parse_mode))


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeUpdate:
    def __init__(self, user_id: int) -> None:
        self.message = _FakeMessage()
        self.effective_user = _FakeUser(user_id)


async def test_birthday_command_escapes_name(db_engine, monkeypatch) -> None:
    """/내생일 응답의 member.name이 escape되어야 한다 (audit #42)."""
    from contextlib import contextmanager

    from sqlalchemy.orm import sessionmaker

    import bot.handlers.query as query
    from bot.handlers.query import birthday_command

    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(query, "get_session", _get_session)

    with _get_session() as s:
        s.add(
            FamilyMember(
                name="철수<b> & 영희",
                telegram_user_id=778899,
                birthday_solar=date(1990, 8, 20),
                active=True,
            )
        )

    try:
        update = _FakeUpdate(778899)
        await birthday_command(update, None)  # type: ignore[arg-type]

        assert update.message.replies
        text, parse_mode = update.message.replies[-1]
        assert parse_mode == "HTML"
        assert "철수&lt;b&gt; &amp; 영희" in text
        assert "철수<b> " not in text
    finally:
        with _get_session() as s:
            s.query(FamilyMember).filter(FamilyMember.telegram_user_id == 778899).delete()


async def test_upcoming_command_rejects_non_member(db_engine, monkeypatch) -> None:
    """등록되지 않은 사용자는 /다음일정으로 알림 큐를 열람할 수 없어야 한다 (audit #9)."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker

    import bot.handlers.query as query
    from bot.handlers.query import upcoming_command

    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(query, "get_session", _get_session)

    # 등록된 구성원이 없는 텔레그램 ID로 호출.
    update = _FakeUpdate(424242)
    await upcoming_command(update, SimpleNamespace(args=[]))  # type: ignore[arg-type]

    assert update.message.replies
    text, _ = update.message.replies[-1]
    assert "등록된 가족 구성원을 찾을 수 없습니다" in text


async def test_upcoming_command_allows_active_member(db_engine, monkeypatch) -> None:
    """등록된 활성 구성원은 정상적으로 /다음일정을 조회할 수 있어야 한다 (audit #9)."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker

    import bot.handlers.query as query
    from bot.handlers.query import upcoming_command

    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(query, "get_session", _get_session)

    with _get_session() as s:
        s.add(FamilyMember(name="회원", telegram_user_id=515151, active=True))

    try:
        update = _FakeUpdate(515151)
        await upcoming_command(update, SimpleNamespace(args=[]))  # type: ignore[arg-type]

        assert update.message.replies
        text, _ = update.message.replies[-1]
        # 권한은 통과했으므로 거부 메시지가 아니어야 한다 (예정 알림 없음 응답).
        assert "등록된 가족 구성원을 찾을 수 없습니다" not in text
        assert "예정된 알림이 없습니다" in text
    finally:
        with _get_session() as s:
            s.query(FamilyMember).filter(FamilyMember.telegram_user_id == 515151).delete()
