"""/start 핸들러의 HTML escape 회귀 테스트 (audit #41).

user.full_name은 외부 사용자가 프로필에서 임의 설정하는 값이므로 그룹 알림에
삽입될 때 escape되어야 한다.
"""

from bot.handlers.basic import start
from shared.config import settings


class _FakeMessage:
    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        pass


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


async def test_start_escapes_full_name_in_group_notice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "group_chat_id", -1001234567890)

    update = _FakeUpdate(42, "<Kim & Lee>")
    context = _FakeContext()

    await start(update, context)  # type: ignore[arg-type]

    assert context.bot.sent
    text = str(context.bot.sent[-1]["text"])
    assert "&lt;Kim &amp; Lee&gt;" in text
    assert "<Kim" not in text
    # 의도된 <b> 마크업은 유지
    assert "<b>" in text
