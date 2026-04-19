from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
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
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Seoul", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    weight_logs: Mapped[list["WeightLog"]] = relationship(back_populates="member")


class ReminderRule(Base):
    """알림 규칙 — 생일/명절/건강검진/커스텀/다이어트리포트."""

    __tablename__ = "reminder_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[ReminderType] = mapped_column(nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # 며칠 전에 알릴지 목록 (예: [14, 7, 3, 1])
    lead_times_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, nullable=False)
    # 타입별 추가 설정 (JSONB)
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
        back_populates="rule", cascade="all, delete-orphan"
    )


class ScheduledNotification(Base):
    """규칙에서 파생된 실제 발송 예정 큐."""

    __tablename__ = "scheduled_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 수동 공지는 rule 없이 발송되므로 nullable
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("reminder_rules.id", ondelete="CASCADE"), nullable=True, index=True
    )
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

    rule: Mapped[ReminderRule] = relationship(back_populates="notifications")


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
