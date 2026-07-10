"""shared.notifier 재시도·429·타임아웃·토큰 마스킹 회귀 테스트 (audit #8, #10).

실제 네트워크 없이 httpx.AsyncClient를 가짜 클라이언트로 교체하고, asyncio.sleep도
no-op으로 바꿔 백오프 대기 없이 즉시 검증한다.
"""

import httpx
import pytest

import shared.notifier as notifier

_URL = "https://api.telegram.org/bot123456:SECRET/sendMessage"


def _resp(status: int, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=json_body if json_body is not None else {},
        request=httpx.Request("POST", _URL),
    )


class _FakeClient:
    """AsyncClient 대체 — post() 호출마다 미리 준비한 응답/예외를 순서대로 돌려준다."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> httpx.Response:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def patched(monkeypatch):
    """가짜 클라이언트 주입 + sleep no-op. (outcomes)를 넣으면 client를 만들어 준다."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(notifier.asyncio, "sleep", fake_sleep)

    def install(outcomes: list) -> _FakeClient:
        fake = _FakeClient(outcomes)
        monkeypatch.setattr(notifier.httpx, "AsyncClient", lambda **kw: fake)
        return fake

    return install, slept


async def test_success_first_try(patched) -> None:
    install, slept = patched
    fake = install([_resp(200, {"ok": True})])

    ok, err = await notifier.send_message(111, "안녕")

    assert (ok, err) == (True, None)
    assert fake.calls == 1
    assert slept == []


async def test_retries_5xx_then_succeeds(patched) -> None:
    install, slept = patched
    fake = install([_resp(500, {"description": "Internal"}), _resp(200, {"ok": True})])

    ok, err = await notifier.send_message(111, "안녕")

    assert ok is True
    assert fake.calls == 2
    assert len(slept) == 1  # 5xx 후 한 번 백오프


async def test_429_respects_retry_after(patched) -> None:
    install, slept = patched
    fake = install(
        [
            _resp(429, {"parameters": {"retry_after": 7}}),
            _resp(200, {"ok": True}),
        ]
    )

    ok, err = await notifier.send_message(111, "안녕")

    assert ok is True
    assert fake.calls == 2
    assert slept == [7.0]  # Retry-After 값만큼 대기


async def test_permanent_4xx_not_retried(patched) -> None:
    install, slept = patched
    fake = install([_resp(400, {"description": "Bad Request: chat not found"})])

    ok, err = await notifier.send_message(0, "안녕")

    assert ok is False
    assert fake.calls == 1  # 4xx는 재시도하지 않음
    assert slept == []
    assert err is not None
    assert "chat not found" in err


async def test_timeout_retries_then_fails(patched) -> None:
    install, slept = patched
    req = httpx.Request("POST", _URL)
    fake = install(
        [
            httpx.ReadTimeout("timed out", request=req),
            httpx.ReadTimeout("timed out", request=req),
            httpx.ReadTimeout("timed out", request=req),
        ]
    )

    ok, err = await notifier.send_message(111, "안녕")

    assert ok is False
    assert fake.calls == notifier._MAX_ATTEMPTS
    assert len(slept) == notifier._MAX_ATTEMPTS - 1
    assert err is not None


async def test_error_masks_bot_token(patched, monkeypatch) -> None:
    """예외 문자열에 담긴 봇 토큰이 반환 에러에 노출되면 안 된다 (audit #10)."""
    monkeypatch.setattr(notifier.settings, "telegram_bot_token", "123456:SECRET")
    install, _ = patched
    req = httpx.Request("POST", _URL)
    # httpx 예외 메시지에 토큰이 박힌 URL이 들어가는 상황을 흉내낸다.
    install([httpx.ConnectError(f"connect fail for {_URL}", request=req)] * notifier._MAX_ATTEMPTS)

    ok, err = await notifier.send_message(111, "안녕")

    assert ok is False
    assert err is not None
    assert "123456:SECRET" not in err
    assert "/bot***" in err


def test_mask_token_helper(monkeypatch) -> None:
    monkeypatch.setattr(notifier.settings, "telegram_bot_token", "123456:SECRET")
    masked = notifier._mask_token(f"error for url '{_URL}'")
    assert "123456:SECRET" not in masked
    assert "***" in masked
