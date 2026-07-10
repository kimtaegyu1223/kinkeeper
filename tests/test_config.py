"""shared.config 시작 시 검증·env_file 절대경로 회귀 테스트 (audit #19, #67, #75)."""

import os

import pytest

from shared.config import Settings

_VALID = dict(
    telegram_bot_token="123456:TOKEN",
    group_chat_id=-1001234567890,
    tz="Asia/Seoul",
)


def _settings(**overrides) -> Settings:
    data = {**_VALID, **overrides}
    return Settings(**data)  # type: ignore[arg-type]


def test_env_file_is_absolute() -> None:
    """env_file이 CWD 상대경로가 아니라 절대경로로 고정돼야 한다 (audit #67)."""
    env_file = Settings.model_config["env_file"]
    assert os.path.isabs(str(env_file))


def test_validate_runtime_passes_for_valid_settings() -> None:
    _settings().validate_runtime()  # 예외 없이 통과해야 한다


def test_validate_runtime_rejects_empty_token() -> None:
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        _settings(telegram_bot_token="").validate_runtime()


def test_validate_runtime_rejects_zero_chat_id() -> None:
    """group_chat_id=0(미설정)이면 모든 알림이 발송 실패하므로 즉시 중단해야 한다 (audit #19)."""
    with pytest.raises(RuntimeError, match="GROUP_CHAT_ID"):
        _settings(group_chat_id=0).validate_runtime()


def test_validate_runtime_rejects_bad_timezone() -> None:
    """잘못된 tz는 generator/rebuild를 붕괴시키므로 시작 시 걸러야 한다 (audit #75)."""
    with pytest.raises(RuntimeError, match="TZ="):
        _settings(tz="Asia/Seoulx").validate_runtime()


def test_validate_runtime_reports_all_errors_at_once() -> None:
    with pytest.raises(RuntimeError) as exc:
        _settings(telegram_bot_token="", group_chat_id=0, tz="Bad/Zone").validate_runtime()
    msg = str(exc.value)
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "GROUP_CHAT_ID" in msg
    assert "TZ=" in msg
