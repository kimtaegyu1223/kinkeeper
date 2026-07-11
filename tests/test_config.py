"""shared.config 시작 시 검증·env_file 절대경로 회귀 테스트 (audit #19, #67, #75)."""

import os

import pytest

from shared.config import Settings

_VALID = dict(
    telegram_bot_token="123456:TOKEN",
    group_chat_id=-1001234567890,
    tz="Asia/Seoul",
    database_url="postgresql+psycopg://family:rotated-secret@localhost:5432/family_notifier",
    admin_password_hash="$2b$12$abcdefghijklmnopqrstuv0123456789ABCDEFGHIJKLMNOPQRS",
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


def test_validate_runtime_rejects_missing_database_url() -> None:
    """DATABASE_URL 미설정은 운영 DB 오접속·뒤늦은 크래시로 이어지므로 막아야 한다 (audit #67)."""
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _settings(database_url="").validate_runtime()


def test_validate_runtime_rejects_unconfigured_database_url() -> None:
    """스킴만 있는 미설정 placeholder(클래스 기본값 그대로)도 거부해야 한다 (audit #67)."""
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _settings(database_url="postgresql+psycopg://").validate_runtime()


def test_validate_runtime_rejects_changeme_database_url() -> None:
    """DATABASE_URL에 기본값(changeme)이 남아 있으면 운영 DB로 오인 접속한다 (audit #67)."""
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _settings(
            database_url="postgresql+psycopg://family:changeme@localhost:5432/family_notifier"
        ).validate_runtime()


def test_validate_runtime_rejects_empty_admin_password_hash() -> None:
    """ADMIN_PASSWORD_HASH가 비어 있으면 관리자 로그인이 원인 불명확 401로만 막힌다 (audit #67)."""
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD_HASH"):
        _settings(admin_password_hash="").validate_runtime()


def test_validate_runtime_reports_all_errors_at_once() -> None:
    with pytest.raises(RuntimeError) as exc:
        _settings(
            telegram_bot_token="",
            group_chat_id=0,
            tz="Bad/Zone",
            database_url="",
            admin_password_hash="",
        ).validate_runtime()
    msg = str(exc.value)
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "GROUP_CHAT_ID" in msg
    assert "TZ=" in msg
    assert "DATABASE_URL" in msg
    assert "ADMIN_PASSWORD_HASH" in msg
