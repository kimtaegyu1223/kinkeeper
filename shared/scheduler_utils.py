"""web 프로세스에서 규칙 즉시 재빌드할 때 쓰는 헬퍼."""

from shared.db import get_session
from shared.generators import rebuild_for_rule as _rebuild_for_rule


def rebuild_for_rule(rule_id: int, horizon_days: int = 60) -> None:
    """세션을 자동으로 열어 rule_id에 해당하는 알림 예약을 즉시 재생성."""
    with get_session() as session:
        _rebuild_for_rule(rule_id, session, horizon_days)
