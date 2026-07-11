import enum


# 운영 주의: 이 enum은 네이티브 PG enum 타입(remindertype, 마이그레이션 50cad0b53392)에
# 매핑된다. 멤버를 추가할 때는 마이그레이션에서
# op.execute("ALTER TYPE remindertype ADD VALUE '<새값>'")를 트랜잭션 밖에서 실행해야 한다
# (ADD VALUE는 트랜잭션 안에서 쓸 수 없음). 멤버 제거·이름 변경은 enum 타입 재생성이 필요하다.
class ReminderType(enum.StrEnum):
    birthday = "birthday"
    holiday = "holiday"
    health_check = "health_check"
    custom = "custom"


class NotificationStatus(enum.StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"
