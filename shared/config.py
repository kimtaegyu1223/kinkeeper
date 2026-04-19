from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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
    schedule_horizon_days: int = 60


settings = Settings()
