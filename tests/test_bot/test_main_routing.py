"""bot/main 라우팅·시작 견고성 회귀 테스트 (audit #55, #76, #36)."""

from types import SimpleNamespace

import pytest

import bot.main as botmain


class _FakeMessage:
    def __init__(self, text: str, chat_id: int = 1, chat_type: str = "private") -> None:
        self.text = text
        self.chat_id = chat_id
        self.chat = SimpleNamespace(type=chat_type)
        self.replies: list[str] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, text: str) -> None:
        self.message = _FakeMessage(text)
        self.effective_user = SimpleNamespace(id=123)


class _FakeContext:
    def __init__(self) -> None:
        self.args: list[str] | None = None


@pytest.fixture
def record_handlers(monkeypatch):
    """라우팅 대상 핸들러를 호출 기록용으로 대체."""
    called: list[str] = []

    def make(name):
        async def handler(update, context):
            called.append(name)

        return handler

    for name in (
        "upcoming_command",
        "birthday_command",
        "health_status_command",
        "health_done_command",
    ):
        monkeypatch.setattr(botmain, name, make(name))
    return called


# ---------------------------------------------------------------------------
# #55 접두 오탐 방지 — 첫 토큰 정확 일치만 명령으로 처리
# ---------------------------------------------------------------------------


async def test_router_ignores_prefix_false_positive(record_handlers) -> None:
    """'/다음일정표 볼래'는 /다음일정으로 오인되면 안 된다 (audit #55)."""
    await botmain._handle_korean_command(_FakeUpdate("/다음일정표 볼래"), _FakeContext())
    assert record_handlers == []


async def test_router_exact_match_routes_and_parses_args(record_handlers) -> None:
    ctx = _FakeContext()
    await botmain._handle_korean_command(_FakeUpdate("/다음일정 30"), ctx)
    assert record_handlers == ["upcoming_command"]
    assert ctx.args == ["30"]


async def test_router_leading_space_still_matches(record_handlers) -> None:
    """선행 공백이 있어도 명령이 조용히 무시되면 안 된다 (audit #55)."""
    await botmain._handle_korean_command(_FakeUpdate("   /다음일정"), _FakeContext())
    assert record_handlers == ["upcoming_command"]


# ---------------------------------------------------------------------------
# #76 메시지 원문 로깅 제거 — 매칭된 명령명·메타데이터만 로깅
# ---------------------------------------------------------------------------


@pytest.fixture
def capture_log(monkeypatch):
    records: list[dict] = []

    class _FakeLog:
        def info(self, event, **kw):
            records.append({"event": event, **kw})

        def warning(self, *a, **k):
            pass

        def exception(self, *a, **k):
            pass

        def error(self, event, **kw):
            records.append({"event": event, **kw})

    monkeypatch.setattr(botmain, "log", _FakeLog())
    return records


async def test_router_does_not_log_non_command_content(capture_log, record_handlers) -> None:
    """명령이 아닌 사적 대화 텍스트는 로깅되지 않아야 한다 (audit #76)."""
    update = _FakeUpdate("우리 이번주 병원 예약 바꿔야 해")
    await botmain._handle_korean_command(update, _FakeContext())
    assert capture_log == []


async def test_router_logs_only_command_metadata(capture_log, record_handlers) -> None:
    """명령 매칭 시에도 원문 text 대신 명령명만 로깅해야 한다 (audit #76)."""
    await botmain._handle_korean_command(_FakeUpdate("/다음일정 7"), _FakeContext())
    assert capture_log
    for rec in capture_log:
        assert "text" not in rec
    assert capture_log[-1]["command"] == "/다음일정"


# ---------------------------------------------------------------------------
# #36 시작 시 rebuild 실패가 프로세스를 죽이지 않음
# ---------------------------------------------------------------------------


async def test_startup_rebuild_retries_then_gives_up(monkeypatch) -> None:
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise RuntimeError("DB 미기동")

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(botmain, "rebuild_upcoming_async", always_fail)
    monkeypatch.setattr(botmain.asyncio, "sleep", fast_sleep)

    # 끝내 실패해도 예외를 전파하지 않아야 한다.
    await botmain._startup_rebuild(max_attempts=3)
    assert calls["n"] == 3


async def test_startup_rebuild_succeeds_first_try(monkeypatch) -> None:
    calls = {"n": 0}

    async def ok():
        calls["n"] += 1

    monkeypatch.setattr(botmain, "rebuild_upcoming_async", ok)
    await botmain._startup_rebuild(max_attempts=3)
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 하드닝: 에러 핸들러 — 예외 타입/요약만 로깅(PII 금지) + 일반 안내 회신
# ---------------------------------------------------------------------------


class _ErrMessage:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.replies: list[str] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        if self.fail:
            raise RuntimeError("회신 실패")
        self.replies.append(text)


async def test_error_handler_logs_type_and_replies(capture_log) -> None:
    msg = _ErrMessage()
    update = SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(id=77))
    context = SimpleNamespace(error=ValueError("사용자 원문<b> 유출되면 안 됨"))

    await botmain._on_error(update, context)

    assert capture_log
    rec = capture_log[-1]
    assert rec["event"] == "핸들러 처리 중 오류"
    assert rec["error_type"] == "ValueError"
    assert rec["chat_id"] == 77
    # update 본문·이름 등은 별도 필드로 로깅되지 않아야 한다.
    for forbidden in ("text", "name", "full_name", "message", "update"):
        assert forbidden not in rec
    # effective_message가 있으면 일반 안내 1줄 회신
    assert msg.replies and "다시 시도" in msg.replies[0]


async def test_error_handler_swallows_reply_failure(capture_log) -> None:
    msg = _ErrMessage(fail=True)
    update = SimpleNamespace(effective_message=msg, effective_chat=None)
    context = SimpleNamespace(error=RuntimeError("boom"))

    # 회신이 실패해도 예외를 전파하지 않아야 한다.
    await botmain._on_error(update, context)
    assert capture_log[-1]["error_type"] == "RuntimeError"


async def test_error_handler_handles_non_update_and_no_error(capture_log) -> None:
    # update가 Update가 아니고 error가 None이어도 안전해야 한다.
    await botmain._on_error(None, SimpleNamespace(error=None))
    rec = capture_log[-1]
    assert rec["error_type"] == "Unknown"
    assert "chat_id" not in rec
