"""scheduled_notifications pending 한정 partial unique index 추가

같은 (rule_id, scheduled_at, target_telegram_id) 및 source_key 에 대해 동시 rebuild로
중복 pending 행이 생기는 것을 DB 레벨에서 막는다 (audit #29). pending 상태만 유니크로
강제하므로 sent/failed/cancelled 이력은 여러 건 남을 수 있다.

인덱스 생성 전 upgrade()가 기존 중복 pending 행을 그룹별 최신 1건만 남기고 cancelled로
자가정리하므로, 운영 DB에 중복 pending이 있어도 CREATE UNIQUE INDEX가 실패하지 않는다.

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


# 인덱스 생성 전, 이미 운영 DB에 남아있을 수 있는 중복 pending을 그룹별 최신 1건만 남기고
# cancelled 처리한다. 구코드의 중복 방지는 앱단 SELECT-후-INSERT(레이스 존재, audit #29)뿐이라
# 동일 (rule_id, scheduled_at, target_telegram_id) 또는 동일 source_key pending이 2건 이상
# 존재할 개연성이 실재한다. 정리하지 않으면 CREATE UNIQUE INDEX가 duplicate key로 실패해
# 단일 트랜잭션(env.py)이 통째로 롤백되고 배포가 중단된다. 감사 이력 보존을 위해 삭제가 아닌
# cancelled로 남긴다.
_DEDUP_RULE_PENDING = """
UPDATE scheduled_notifications
SET status = 'cancelled'
WHERE id IN (
    SELECT id FROM (
        SELECT id, row_number() OVER (
            PARTITION BY rule_id, scheduled_at, target_telegram_id
            ORDER BY id DESC
        ) AS rn
        FROM scheduled_notifications
        WHERE status = 'pending' AND rule_id IS NOT NULL
    ) ranked
    WHERE ranked.rn > 1
)
"""

_DEDUP_SOURCE_PENDING = """
UPDATE scheduled_notifications
SET status = 'cancelled'
WHERE id IN (
    SELECT id FROM (
        SELECT id, row_number() OVER (
            PARTITION BY source_key
            ORDER BY id DESC
        ) AS rn
        FROM scheduled_notifications
        WHERE status = 'pending' AND source_key IS NOT NULL
    ) ranked
    WHERE ranked.rn > 1
)
"""


def upgrade() -> None:
    """Upgrade schema."""
    # 인덱스 생성 전 중복 pending 자가정리 (그룹별 최신 id만 pending 유지).
    op.execute(_DEDUP_RULE_PENDING)
    op.execute(_DEDUP_SOURCE_PENDING)
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
