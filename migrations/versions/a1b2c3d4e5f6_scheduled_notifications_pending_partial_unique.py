"""scheduled_notifications pending 한정 partial unique index 추가

같은 (rule_id, scheduled_at, target_telegram_id) 및 source_key 에 대해 동시 rebuild로
중복 pending 행이 생기는 것을 DB 레벨에서 막는다 (audit #29). pending 상태만 유니크로
강제하므로 sent/failed/cancelled 이력은 여러 건 남을 수 있다.

주의: 적용 전 기존 중복 pending 행을 먼저 정리해야 인덱스 생성이 성공한다.

Revision ID: a1b2c3d4e5f6
Revises: 699ca1657399
Create Date: 2026-07-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "699ca1657399"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "uq_sched_notif_rule_pending",
        "scheduled_notifications",
        ["rule_id", "scheduled_at", "target_telegram_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND rule_id IS NOT NULL"),
    )
    op.create_index(
        "uq_sched_notif_source_pending",
        "scheduled_notifications",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND source_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_sched_notif_source_pending", table_name="scheduled_notifications")
    op.drop_index("uq_sched_notif_rule_pending", table_name="scheduled_notifications")
