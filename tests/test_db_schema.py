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


def test_insert_family_member(db_session) -> None:
    """FamilyMember insert/조회 기본 동작 확인."""
    member = FamilyMember(name="테스트유저", timezone="Asia/Seoul")
    db_session.add(member)
    db_session.flush()

    result = db_session.get(FamilyMember, member.id)
    assert result is not None
    assert result.name == "테스트유저"
    assert result.active is True
