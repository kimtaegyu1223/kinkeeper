from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트의 .env를 CWD와 무관하게 로드하도록 절대경로로 고정한다.
# 상대경로 '.env'는 루트 밖에서 실행하면 조용히 무시되고 전 필드가 기본값이 된다 (audit #67).
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

# DATABASE_URL 미설정 시의 플레이스홀더. 완전한 빈 문자열("")로 두면 shared/db.py가 모듈
# 임포트 시점에 create_engine("")을 호출해 ArgumentError로 전체 테스트 수집이 깨진다.
# 스킴만 있고 자격증명/호스트가 전혀 없는 이 값은 create_engine()에서는 안전하게 파싱되지만
# (실제 연결 시도는 지연 실행) 실제 자격증명과는 절대 겹치지 않으며, validate_runtime()이
# 기동 시점에 반드시 걸러낸다.
_UNCONFIGURED_DATABASE_URL = "postgresql+psycopg://"


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
    # 실제 자격증명을 기본값으로 두지 않는다: 과거 'family:changeme@localhost/...' 기본값이
    # docker-compose의 기본 자격증명과 우연히 일치해, .env가 무시돼도 조용히 실제 로컬
    # 운영 DB에 접속해 겉보기 정상 기동할 수 있었다 (audit #67). 값은 반드시 .env/환경변수로
    # 채워야 하며, 미설정 placeholder이거나 'changeme'가 남아 있으면 validate_runtime()이
    # 기동을 거부한다.
    database_url: str = _UNCONFIGURED_DATABASE_URL

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
        발송 실패하거나 rebuild가 예외로 붕괴한다 (audit #19, #75). DATABASE_URL이 미설정
        placeholder이거나 기본값(changeme)이 남아 있으면 실제 운영 DB로 오인 접속하거나
        기동 후 엉뚱한 곳에서 실패하고, ADMIN_PASSWORD_HASH가 비어 있으면 관리자 로그인이
        원인 불명확한 401로만 막힌다 (audit #67) — 모두 시작 시점에 명확한 에러로 막는다.
        import 시점이 아니라 시작 시점에서만 검증해 .env 없이 임포트만 하는 테스트·CI를
        깨지 않는다.
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
        if not self.database_url or self.database_url == _UNCONFIGURED_DATABASE_URL:
            errors.append("DATABASE_URL이 설정되지 않았습니다. 운영 DB 접속 정보를 지정하세요.")
        elif "changeme" in self.database_url:
            errors.append(
                "DATABASE_URL에 기본값(changeme)이 남아 있습니다. "
                "실제 운영 DB 비밀번호로 교체하세요."
            )
        if not self.admin_password_hash:
            errors.append("ADMIN_PASSWORD_HASH가 비어 있습니다. 관리자 비밀번호 해시를 설정하세요.")
        if errors:
            raise RuntimeError("설정 오류:\n- " + "\n- ".join(errors))


settings = Settings()
