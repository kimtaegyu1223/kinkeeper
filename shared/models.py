from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from shared.enums import NotificationStatus, ReminderType


class Base(DeclarativeBase):
    pass


class FamilyMember(Base):
    """가족 구성원."""

    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    birthday_solar: Mapped[date | None] = mapped_column(nullable=True)
    birthday_lunar: Mapped[date | None] = mapped_column(nullable=True)
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True)  # M, F, None=미설정
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Seoul", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    weight_logs: Mapped[list["WeightLog"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", passive_deletes=True
    )
    health_records: Mapped[list["HealthCheckRecord"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", passive_deletes=True
    )
    health_configs: Mapped[list["MemberHealthCheckConfig"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", passive_deletes=True
    )


class ReminderRule(Base):
    """알림 규칙 — 생일/명절/건강검진/커스텀/다이어트리포트."""

    __tablename__ = "reminder_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[ReminderType] = mapped_column(nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    lead_times_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    notifications: Mapped[list["ScheduledNotification"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", passive_deletes=True
    )


class ScheduledNotification(Base):
    """규칙에서 파생된 실제 발송 예정 큐."""

    __tablename__ = "scheduled_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("reminder_rules.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 건강검진 등 rule 없이 생성되는 알림의 중복 방지 키
    source_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    target_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        default=NotificationStatus.pending, nullable=False, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule: Mapped[ReminderRule | None] = relationship(back_populates="notifications")


class WeightLog(Base):
    """가족 구성원 몸무게 기록."""

    __tablename__ = "weight_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("family_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    weight_kg: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    member: Mapped[FamilyMember] = relationship(back_populates="weight_logs")


class AdminBroadcast(Base):
    """관리자 수동 공지 발송 기록."""

    __tablename__ = "admin_broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_by: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HealthCheckType(Base):
    """건강검진 항목 (관리자가 추가/수정 가능)."""

    __tablename__ = "health_check_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    period_years: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True)  # M, F, None=모두
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    records: Mapped[list["HealthCheckRecord"]] = relationship(
        back_populates="check_type", cascade="all, delete-orphan", passive_deletes=True
    )
    member_configs: Mapped[list["MemberHealthCheckConfig"]] = relationship(
        back_populates="check_type", cascade="all, delete-orphan", passive_deletes=True
    )


class HealthCheckRecord(Base):
    """가족 구성원 건강검진 완료 기록."""

    __tablename__ = "health_check_records"
    __table_args__ = (
        UniqueConstraint("member_id", "check_type_id", "checked_at", name="uq_health_record"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("family_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    check_type_id: Mapped[int] = mapped_column(
        ForeignKey("health_check_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checked_at: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[FamilyMember] = relationship(back_populates="health_records")
    check_type: Mapped[HealthCheckType] = relationship(back_populates="records")


class MemberHealthCheckConfig(Base):
    """구성원별 건강검진 주기 설정 (없으면 HealthCheckType 기본값 사용)."""

    __tablename__ = "member_health_check_configs"
    __table_args__ = (
        UniqueConstraint("member_id", "check_type_id", name="uq_member_check_config"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("family_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    check_type_id: Mapped[int] = mapped_column(
        ForeignKey("health_check_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_years: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = 기본값 사용
    # False = 이 사람은 이 검진 알림 끔
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    member: Mapped["FamilyMember"] = relationship(back_populates="health_configs")
    check_type: Mapped["HealthCheckType"] = relationship(back_populates="member_configs")
