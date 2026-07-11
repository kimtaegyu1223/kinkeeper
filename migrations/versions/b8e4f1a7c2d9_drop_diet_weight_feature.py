"""drop diet/weight feature

다이어트/몸무게 기능 폐기 결정(2026-07-11)에 따라 관련 스키마를 제거한다:
- weight_logs 테이블 드롭
- family_members.height_cm / diet_active 컬럼 드롭
- 네이티브 enum remindertype에서 diet_report 값 제거

enum 값 제거는 ALTER TYPE ... DROP VALUE가 없으므로 새 타입 생성 → USING 캐스트 →
구 타입 드롭 → 이름 변경 방식으로 처리한다. 이 전 과정은 트랜잭션 안에서 안전하게
실행된다(ADD VALUE와 달리 CREATE TYPE/ALTER COLUMN은 트랜잭션 제약이 없음).
운영 DB에 diet_report 규칙이 0건임을 확인한 전제에서 USING 캐스트가 안전하다
(diet_report 행이 남아 있으면 새 타입으로 캐스트가 실패한다).

Revision ID: b8e4f1a7c2d9
Revises: c7f3a9e21b04
Create Date: 2026-07-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4f1a7c2d9"
down_revision: str | Sequence[str] | None = "c7f3a9e21b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ENUM = ("birthday", "holiday", "health_check", "custom", "diet_report")
_NEW_ENUM = ("birthday", "holiday", "health_check", "custom")


def _replace_remindertype(values: Sequence[str]) -> None:
    """remindertype enum을 values로 재생성한다(구 타입 드롭 후 이름 승계)."""
    quoted = ", ".join(f"'{v}'" for v in values)
    op.execute(f"CREATE TYPE remindertype_new AS ENUM ({quoted})")
    op.execute(
        "ALTER TABLE reminder_rules ALTER COLUMN type TYPE remindertype_new "
        "USING type::text::remindertype_new"
    )
    op.execute("DROP TYPE remindertype")
    op.execute("ALTER TYPE remindertype_new RENAME TO remindertype")


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_weight_logs_member_id"), table_name="weight_logs")
    op.drop_table("weight_logs")
    op.drop_column("family_members", "diet_active")
    op.drop_column("family_members", "height_cm")
    _replace_remindertype(_NEW_ENUM)


def downgrade() -> None:
    """Downgrade schema."""
    _replace_remindertype(_OLD_ENUM)
    op.add_column(
        "family_members",
        sa.Column("height_cm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "family_members",
        sa.Column(
            "diet_active",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.create_table(
        "weight_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("weight_kg", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["family_members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_weight_logs_member_id"), "weight_logs", ["member_id"], unique=False)
