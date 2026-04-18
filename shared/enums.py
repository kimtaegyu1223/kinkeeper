import enum


class ReminderType(enum.StrEnum):
    birthday = "birthday"
    holiday = "holiday"
    health_check = "health_check"
    custom = "custom"
    diet_report = "diet_report"


class NotificationStatus(enum.StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"
