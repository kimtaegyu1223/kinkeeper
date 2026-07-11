"""/start 핸들러 회귀 테스트.

- full_name HTML escape (audit #41)
- /start 남용 차단(하드닝): 그룹 알림 인메모리 dedup·미등록 사용자 응답 쿨다운·
  등록 멤버 그룹 재알림 억제·신규 알림 로그 PII 제거
"""

import pytest

import bot.handlers.basic as basic
from shared.config import settings


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


class _FakeUser:
    def __init__(self, user_id: int, full_name: str) -> None:
        self.id = user_id
        self.full_name = full_name


class _FakeUpdate:
    def __init__(self, user_id: int, full_name: str) -> None:
        self.message = _FakeMessage()
        self.effective_user = _FakeUser(user_id, full_name)


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str, parse_mode: str | None = None) -> None:
        self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


class _FakeContext:
    def __init__(self) -> None:
        self.bot = _FakeBot()


@pytest.fixture(autouse=True)
def _reset_start_state(monkeypatch):
    """인메모리 상태를 초기화하고, 기본은 미등록 사용자로 둔다(DB 미접근)."""
    basic._seen_user_ids.clear()
    basic._last_response_at.clear()
    # 기본값: 미등록 사용자. get_session/DB를 건드리지 않도록 멤버 조회를 대체한다.
    monkeypatch.setattr(basic, "_is_registered_active_member", lambda uid: False)
    yield
    basic._seen_user_ids.clear()
    basic._last_response_at.clear()


# ---------------------------------------------------------------------------
# #41 full_name escape — 그룹 알림에 프로필명이 그대로 주입되면 안 된다
# ---------------------------------------------------------------------------


async def test_start_escapes_full_name_in_group_notice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "group_chat_id", -1001234567890)

    update = _FakeUpdate(42, "<Kim & Lee>")
    context = _FakeContext()

    await basic.start(update, context)  # type: ignore[arg-type]

    assert context.bot.sent
    text = str(context.bot.sent[-1]["text"])
    assert "&lt;Kim &amp; Lee&gt;" in text
    assert "<Kim" not in text
    # 의도된 <b> 마크업은 유지
    assert "<b>" in text


# ---------------------------------------------------------------------------
# 하드닝: 그룹 신규유저 알림 인메모리 dedup(도배 차단)
# ---------------------------------------------------------------------------


async def test_start_group_notice_deduplicated(monkeypatch) -> None:
    """같은 미등록 user_id의 반복 /start는 그룹 알림을 한 번만 보낸다."""
    monkeypatch.setattr(settings, "group_chat_id", -100123)
    clock = {"t": 1000.0}
    monkeypatch.setattr(basic.time, "monotonic", lambda: clock["t"])

    context = _FakeContext()

    await basic.start(_FakeUpdate(42, "홍길동"), context)  # type: ignore[arg-type]
    assert len(context.bot.sent) == 1

    # 쿨다운을 지나 다시 호출해도 그룹 재알림은 없어야 한다(dedup).
    clock["t"] += basic._START_COOLDOWN_SECONDS + 1
    await basic.start(_FakeUpdate(42, "홍길동"), context)  # type: ignore[arg-type]
    assert len(context.bot.sent) == 1


# ---------------------------------------------------------------------------
# 하드닝: 미등록 사용자 응답 쿨다운(쿨다운 중 조용히 무시)
# ---------------------------------------------------------------------------


async def test_start_cooldown_silently_ignores(monkeypatch) -> None:
    monkeypatch.setattr(settings, "group_chat_id", -100123)
    clock = {"t": 500.0}
    monkeypatch.setattr(basic.time, "monotonic", lambda: clock["t"])

    context = _FakeContext()

    await basic.start(_FakeUpdate(7, "김철수"), context)  # type: ignore[arg-type]
    assert len(context.bot.sent) == 1

    # 쿨다운(30초) 이내 재호출 — 개인 회신도 그룹 알림도 없어야 한다.
    clock["t"] += 5
    update2 = _FakeUpdate(7, "김철수")
    await basic.start(update2, context)  # type: ignore[arg-type]
    assert update2.message.replies == []
    assert len(context.bot.sent) == 1


def test_cooldown_helpers() -> None:
    basic._last_response_at.clear()
    assert not basic._on_cooldown(1, now=100.0)
    basic._mark_responded(1, now=100.0)
    assert basic._on_cooldown(1, now=120.0)  # 20s < 30s
    assert not basic._on_cooldown(1, now=131.0)  # 31s >= 30s


# ---------------------------------------------------------------------------
# 하드닝: 등록된 활성 멤버는 그룹 재알림 없이 환영만
# ---------------------------------------------------------------------------


async def test_start_registered_member_no_group_notice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "group_chat_id", -100123)
    monkeypatch.setattr(basic, "_is_registered_active_member", lambda uid: True)
    monkeypatch.setattr(basic.time, "monotonic", lambda: 0.0)

    context = _FakeContext()
    update = _FakeUpdate(99, "엄마")
    await basic.start(update, context)  # type: ignore[arg-type]

    # 등록 멤버에게는 그룹 신규유저 알림을 보내지 않는다.
    assert context.bot.sent == []
    # 개인 환영 회신은 있어야 한다.
    assert update.message.replies


# ---------------------------------------------------------------------------
# 하드닝(#PII): 신규 사용자 알림 로그에 이름이 남지 않고 user_id만 남는다
# ---------------------------------------------------------------------------


async def test_start_group_notice_log_omits_name(monkeypatch) -> None:
    monkeypatch.setattr(settings, "group_chat_id", -100123)
    monkeypatch.setattr(basic.time, "monotonic", lambda: 0.0)

    records: list[dict] = []

    class _Log:
        def info(self, event, **kw):
            records.append({"event": event, **kw})

        def warning(self, *a, **k):
            pass

    monkeypatch.setattr(basic, "log", _Log())

    context = _FakeContext()
    await basic.start(_FakeUpdate(55, "박영희"), context)  # type: ignore[arg-type]

    assert records
    rec = records[-1]
    assert rec["user_id"] == 55
    assert "name" not in rec
    assert "박영희" not in str(rec)
