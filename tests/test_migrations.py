"""마이그레이션 체인·스키마 정합·가역성 회귀 테스트 (audit #21, #51, #71).

빈 testcontainer PostgreSQL에 실제로 `alembic upgrade head`를 돌려
(1) 체인이 처음부터 끝까지 깨지지 않고, (2) 결과 스키마가 Base.metadata와
어긋나지 않으며, (3) 최신 revision들의 downgrade가 실제로 되돌리는지 검증한다.

alembic.ini의 sqlalchemy.url은 env.py가 shared.config.settings.database_url로
덮어쓰므로, 운영 URL 대신 컨테이너 URL을 settings에 주입한다.
컨테이너는 모듈 스코프로 하나만 띄워 재사용한다(스위트 시간 억제).
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from testcontainers.postgres import PostgresContainer

from shared.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# c7f3a9e21b04(timezone drop) / a1b2c3d4e5f6(partial unique) 직전 리비전.
# 여기까지 downgrade하면 최신 2개 revision의 downgrade가 모두 실행된다.
_BEFORE_LAST_TWO = "699ca1657399"


@pytest.fixture(scope="module")
def migration_env():
    """마이그레이션 전용 빈 PostgreSQL 컨테이너 + alembic Config 팩토리.

    conftest의 db_engine은 create_all로 스키마를 채우므로 재사용하면 upgrade가 충돌한다.
    마이그레이션은 반드시 빈 DB에서 검증해야 하므로 전용 컨테이너를 쓴다.
    """
    import shared.config

    with PostgresContainer("postgres:16-alpine") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql+psycopg")
        original = shared.config.settings.database_url
        shared.config.settings.database_url = url
        try:
            engine = create_engine(url, pool_pre_ping=True)

            def make_config() -> Config:
                return Config(str(PROJECT_ROOT / "alembic.ini"))

            yield make_config, engine
            engine.dispose()
        finally:
            shared.config.settings.database_url = original


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _index_names(engine, table: str) -> set[str]:
    insp = inspect(engine)
    names = {ix["name"] for ix in insp.get_indexes(table)}
    names |= {uc["name"] for uc in insp.get_unique_constraints(table)}
    return names


def test_downgrade_base_then_upgrade_head(migration_env) -> None:
    """downgrade base 후 재차 upgrade head가 성공해야 함 (audit #71).

    초기 마이그레이션의 downgrade가 네이티브 enum 타입(remindertype/notificationstatus)을
    드롭하지 않으면, 재-upgrade 시 CREATE TYPE이 DuplicateObject로 실패해 여기서 예외가 난다.
    """
    make_config, _ = migration_env
    cfg = make_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_head_schema_matches_metadata(migration_env) -> None:
    """upgrade head 결과 스키마가 Base.metadata와 어긋나지 않아야 함 (audit #21, #51).

    마이그레이션과 모델이 드리프트하면 운영 DB(마이그레이션 기반)와 테스트/ORM
    (metadata 기반)이 달라져 조용한 버그를 낳는다. 테이블·컬럼·핵심 인덱스를 대조한다.
    """
    make_config, engine = migration_env
    command.upgrade(make_config(), "head")

    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    model_tables = set(Base.metadata.tables)

    # 모델이 정의한 테이블은 모두 마이그레이션으로 생성돼 있어야 한다.
    assert model_tables <= db_tables, model_tables - db_tables

    # 테이블별 컬럼 집합이 정확히 일치해야 한다(누락/잔존 컬럼 = 드리프트).
    for table in model_tables:
        expected = set(Base.metadata.tables[table].columns.keys())
        actual = _columns(engine, table)
        assert expected == actual, f"{table}: {expected ^ actual}"

    # 부분 유니크 인덱스(audit #29)와 명명 유니크 제약이 실제로 존재해야 한다.
    sched_idx = _index_names(engine, "scheduled_notifications")
    assert {"uq_sched_notif_rule_pending", "uq_sched_notif_source_pending"} <= sched_idx
    assert "uq_health_record" in _index_names(engine, "health_check_records")
    assert "uq_member_check_config" in _index_names(engine, "member_health_check_configs")


def test_latest_two_revisions_downgrade(migration_env) -> None:
    """최신 2개 revision(c7f3a9e21b04, a1b2c3d4e5f6)의 downgrade가 실제로 되돌려야 함.

    no-throw만이 아니라 효과 역전을 확인한다: timezone 컬럼 재생성/제거,
    부분 유니크 인덱스 제거/재생성.
    """
    make_config, engine = migration_env
    cfg = make_config()

    command.upgrade(cfg, "head")
    # head 상태: timezone 없음, 부분 유니크 인덱스 있음.
    assert "timezone" not in _columns(engine, "family_members")
    assert "uq_sched_notif_rule_pending" in _index_names(engine, "scheduled_notifications")

    # 최신 2개 revision을 되감는다.
    command.downgrade(cfg, _BEFORE_LAST_TWO)
    assert "timezone" in _columns(engine, "family_members")
    assert "uq_sched_notif_rule_pending" not in _index_names(engine, "scheduled_notifications")

    # 다시 head로 올려 상태가 복원되는지 확인(다음 테스트/재실행 오염 방지 포함).
    command.upgrade(cfg, "head")
    assert "timezone" not in _columns(engine, "family_members")
    assert "uq_sched_notif_rule_pending" in _index_names(engine, "scheduled_notifications")
