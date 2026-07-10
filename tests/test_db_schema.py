from sqlalchemy import inspect

from shared.models import (
    AdminBroadcast,
    FamilyMember,
    ReminderRule,
    ScheduledNotification,
    WeightLog,
)


def test_all_tables_exist(db_engine) -> None:
    """마이그레이션 없이 모델에서 생성한 테이블이 모두 존재하는지 확인."""
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    expected = {
        FamilyMember.__tablename__,
        ReminderRule.__tablename__,
        ScheduledNotification.__tablename__,
        WeightLog.__tablename__,
        AdminBroadcast.__tablename__,
    }
    assert expected.issubset(tables)


def test_diet_active_has_server_default(db_engine) -> None:
    """diet_active의 server_default가 마이그레이션과 일치해야 함 (audit #70).

    모델에 server_default가 없으면 create_all 스키마엔 DEFAULT가 없어 마이그레이션 기반
    운영 DB와 드리프트가 나고, diet_active를 생략한 raw INSERT가 NOT NULL 위반으로 실패한다.
    """
    inspector = inspect(db_engine)
    columns = {c["name"]: c for c in inspector.get_columns("family_members")}
    default = columns["diet_active"]["default"]
    assert default is not None
    assert "false" in default.lower()


def test_insert_family_member(db_session) -> None:
    """FamilyMember insert/조회 기본 동작 확인."""
    member = FamilyMember(name="테스트유저")
    db_session.add(member)
    db_session.flush()

    result = db_session.get(FamilyMember, member.id)
    assert result is not None
    assert result.name == "테스트유저"
    assert result.active is True
