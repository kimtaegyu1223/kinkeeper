"""drop write-only family_members.timezone column

timezone 컬럼은 저장만 되고 어디서도 읽히지 않는다(모든 시각 계산은 전역 settings.tz
사용). 가족 전원이 동일 타임존을 쓰는 맥락상 제거한다 (audit #3).

Revision ID: c7f3a9e21b04
Revises: a1b2c3d4e5f6
Create Date: 2026-07-10 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f3a9e21b04"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("family_members", "timezone")


def downgrade() -> None:
    """Downgrade schema."""
    # server_default로 기존 행의 NOT NULL을 만족시킨 뒤 기본값을 떼어
    # 원래 스키마(default 없는 NOT NULL)와 동일하게 되돌린다.
    op.add_column(
        "family_members",
        sa.Column(
            "timezone",
            sa.String(length=50),
            nullable=False,
            server_default="Asia/Seoul",
        ),
    )
    op.alter_column("family_members", "timezone", server_default=None)
