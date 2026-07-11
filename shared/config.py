from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트의 .env를 CWD와 무관하게 로드하도록 절대경로로 고정한다.
# 상대경로 '.env'는 루트 밖에서 실행하면 조용히 무시되고 전 필드가 기본값이 된다 (audit #67).
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # docker-compose 전용 변수(POSTGRES_*) 무시
    )

    # Telegram
    telegram_bot_token: str = ""
    group_chat_id: int = 0  # 그룹 채널 ID (음수, 예: -1001234567890)

    # Database
    database_url: str = "postgresql+psycopg://family:changeme@localhost:5432/family_notifier"

    # Admin web (HTTP Basic)
    admin_user: str = "admin"
    admin_password_hash: str = ""

    # App
    tz: str = "Asia/Seoul"
    log_level: str = "INFO"
    schedule_horizon_days: int = 90

    def validate_runtime(self) -> None:
        """프로세스 시작 시 호출 — 필수 설정 누락/오류면 명확한 에러로 즉시 중단한다.

        빈 토큰·group_chat_id=0(미설정)·잘못된 tz는 그대로 두면 모든 알림이 조용히
        발송 실패하거나 rebuild가 예외로 붕괴한다 (audit #19, #75). import 시점이 아니라
        시작 시점에서만 검증해 .env 없이 임포트만 하는 테스트·CI를 깨지 않는다.
        """
        errors: list[str] = []
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN이 비어 있습니다.")
        if self.group_chat_id == 0:
            errors.append("GROUP_CHAT_ID가 설정되지 않았습니다(0). 그룹 채널 ID를 지정하세요.")
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            errors.append(f"TZ='{self.tz}'가 유효한 시간대가 아닙니다: {e}")
        if errors:
            raise RuntimeError("설정 오류:\n- " + "\n- ".join(errors))


settings = Settings()
