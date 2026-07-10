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
from sqlalchemy import create_engine, inspect, text
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


def test_partial_unique_upgrade_dedups_existing_pending(migration_env) -> None:
    """중복 pending이 있어도 a1b2c3d4e5f6 upgrade가 성공하고 그룹별 최신 1건만 pending으로 남는다.

    운영 구코드의 중복 방지는 앱단 SELECT-후-INSERT(레이스 존재)뿐이라 동일
    (rule_id, scheduled_at, target) 또는 동일 source_key pending이 2건 이상 존재할 수 있다.
    자가정리(UPDATE ... cancelled)가 없으면 CREATE UNIQUE INDEX가 duplicate key로 실패해
    단일 트랜잭션이 롤백되고 배포가 중단된다 (audit #29 마이그레이션 자가정리).
    """
    make_config, engine = migration_env
    cfg = make_config()

    # 파셜 유니크 인덱스 직전 리비전으로 내려 중복 pending을 심는다(인덱스가 없어 삽입 가능).
    command.downgrade(cfg, _BEFORE_LAST_TWO)
    assert "uq_sched_notif_rule_pending" not in _index_names(engine, "scheduled_notifications")

    with engine.begin() as conn:
        rule_id = conn.execute(
            text(
                "INSERT INTO reminder_rules (type, title, lead_times_days, config, active) "
                "VALUES ('birthday', '중복테스트', '{0}', '{}', true) RETURNING id"
            )
        ).scalar_one()
        # 동일 (rule_id, scheduled_at, target) pending 2건
        conn.execute(
            text(
                "INSERT INTO scheduled_notifications "
                "(rule_id, scheduled_at, target_telegram_id, message, status) VALUES "
                "(:rid, '2026-08-01 00:00:00+00', 123, 'rule-old', 'pending'), "
                "(:rid, '2026-08-01 00:00:00+00', 123, 'rule-new', 'pending')"
            ),
            {"rid": rule_id},
        )
        # 동일 source_key pending 2건
        conn.execute(
            text(
                "INSERT INTO scheduled_notifications "
                "(source_key, scheduled_at, target_telegram_id, message, status) VALUES "
                "('hc:monthly:group:2026-08-01', '2026-08-01 00:00:00+00', 999, 'src-old', "
                "'pending'), "
                "('hc:monthly:group:2026-08-01', '2026-08-01 00:00:00+00', 999, 'src-new', "
                "'pending')"
            )
        )

    # 중복 pending이 있는데도 upgrade가 성공해야 한다.
    command.upgrade(cfg, "head")
    assert "uq_sched_notif_rule_pending" in _index_names(engine, "scheduled_notifications")
    assert "uq_sched_notif_source_pending" in _index_names(engine, "scheduled_notifications")

    with engine.connect() as conn:
        # 그룹별로 최신(가장 큰 id, message ...-new) 1건만 pending, 나머지는 cancelled.
        rule_rows = conn.execute(
            text(
                "SELECT message, status::text FROM scheduled_notifications "
                "WHERE rule_id = :rid ORDER BY id"
            ),
            {"rid": rule_id},
        ).all()
        assert sorted((m, s) for m, s in rule_rows) == [
            ("rule-new", "pending"),
            ("rule-old", "cancelled"),
        ]

        src_rows = conn.execute(
            text(
                "SELECT message, status::text FROM scheduled_notifications "
                "WHERE source_key = 'hc:monthly:group:2026-08-01' ORDER BY id"
            )
        ).all()
        assert sorted((m, s) for m, s in src_rows) == [
            ("src-new", "pending"),
            ("src-old", "cancelled"),
        ]

    # 뒷 테스트/재실행 오염 방지를 위해 심은 행을 정리한다(스키마는 head 유지).
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM scheduled_notifications"))
        conn.execute(text("DELETE FROM reminder_rules WHERE id = :rid"), {"rid": rule_id})
