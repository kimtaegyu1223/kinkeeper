"""마이그레이션 가역성 회귀 테스트."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(monkeypatch):
    """마이그레이션 전용 빈 PostgreSQL 컨테이너를 띄우고 alembic Config를 반환.

    conftest의 db_engine은 create_all로 스키마를 채우므로 재사용하면 upgrade가 충돌한다.
    마이그레이션은 반드시 빈 DB에서 검증해야 하므로 전용 컨테이너를 쓴다.
    """
    with PostgresContainer("postgres:16-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+psycopg")
        # env.py가 shared.config.settings.database_url을 읽어 alembic.ini의 url을 덮어쓴다.
        import shared.config

        monkeypatch.setattr(shared.config.settings, "database_url", url)
        yield Config(str(PROJECT_ROOT / "alembic.ini"))


def test_downgrade_base_then_upgrade_head(alembic_config) -> None:
    """downgrade base 후 재차 upgrade head가 성공해야 함 (audit #71).

    초기 마이그레이션의 downgrade가 네이티브 enum 타입(remindertype/notificationstatus)을
    드롭하지 않으면, 재-upgrade 시 CREATE TYPE이 DuplicateObject로 실패해 여기서 예외가 난다.
    """
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
