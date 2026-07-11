"""웹 폼 검증·CSRF·2/29 회귀 테스트 (audit #18, #25, #37, #45, #46, #47, #58, #59, #61, #62,
#77, #78, #79).

- #18/#59: 규칙 폼의 hour 범위·run_at 형식·알 수 없는 type이 500 대신 400.
- #25: 2/29 검진 기록이 있어도 검진 페이지가 500 없이 렌더된다(2/28 폴백).
- #37: 상태변경 POST가 Sec-Fetch-Site cross-site면 CSRF로 차단된다.
- #45: 음력 2/30 생일 입력이 500 대신 400.
- #46: 잘못된 checked_at/중복 검진 기록/중복 항목명이 500 대신 400.
- #47: 잘못된 telegram_user_id/중복 telegram_user_id가 500 대신 400.
- #58: _parse_int_list가 '--3','²' 같은 토큰을 무시하고 crash하지 않는다.
- #61: 없는 id 조회/수정이 404.
- #77: name/title/검진 주기가 DB 컬럼 길이·합리적 범위를 넘으면 500(DataError) 대신 400.
- #78: active 체크박스가 bool(str) 오판정("false" 문자열도 True) 없이 파싱된다.
- #79: 공지 메시지가 텔레그램 한도(4096자)를 넘으면 500 대신 400.

라우트는 shared.db.get_session(전역 엔진)을 쓰므로 테스트 컨테이너 엔진으로
monkeypatch한다(test_rules.py 패턴 재사용).
"""

from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import web.routes.broadcast as broadcast_route
import web.routes.health_checks as health_route
import web.routes.members as members_route
import web.routes.rules as rules_route
from shared.config import settings
from shared.models import (
    AdminBroadcast,
    FamilyMember,
    HealthCheckRecord,
    HealthCheckType,
    MemberHealthCheckConfig,
    ReminderRule,
    ScheduledNotification,
)
from web.auth import verify_admin
from web.form_utils import parse_checkbox
from web.main import app
from web.routes.rules import _parse_int_list


@pytest.fixture
def client(db_engine, monkeypatch):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    for mod in (members_route, rules_route, health_route, broadcast_route):
        monkeypatch.setattr(mod, "get_session", _get_session)
    monkeypatch.setattr(settings, "group_chat_id", -1001234567890)
    app.dependency_overrides[verify_admin] = lambda: "admin"

    # 500 여부를 응답 코드로 검사하기 위해 예외 재발생을 끈다.
    yield TestClient(app, raise_server_exceptions=False), Session

    app.dependency_overrides.clear()
    with _get_session() as s:
        s.query(ScheduledNotification).delete()
        s.query(AdminBroadcast).delete()
        s.query(HealthCheckRecord).delete()
        s.query(MemberHealthCheckConfig).delete()
        s.query(HealthCheckType).delete()
        s.query(ReminderRule).delete()
        s.query(FamilyMember).delete()


# ── form_utils 헬퍼 (#77, #78) ────────────────────────────────────


def test_parse_checkbox_values() -> None:
    """"on"/"1"/"true"(대소문자 무관)만 True, 그 외(특히 "false"/"0")는 False (audit #78)."""
    assert parse_checkbox("on") is True
    assert parse_checkbox("1") is True
    assert parse_checkbox("true") is True
    assert parse_checkbox("True") is True
    assert parse_checkbox(" ON ") is True
    assert parse_checkbox("false") is False
    assert parse_checkbox("0") is False
    assert parse_checkbox("") is False
    assert parse_checkbox(None) is False


# ── members (#45, #47, #61, #77, #78) ─────────────────────────────


def test_bad_telegram_user_id_returns_400(client) -> None:
    """비숫자 telegram_user_id는 500이 아니라 400 (audit #47)."""
    test_client, Session = client
    resp = test_client.post(
        "/members/new",
        data={"name": "홍길동", "telegram_user_id": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(FamilyMember).count() == 0


def test_member_name_too_long_returns_400(client) -> None:
    """50자(FamilyMember.name 컬럼 길이)를 넘는 이름은 500(DataError) 대신 400 (audit #77)."""
    test_client, Session = client
    resp = test_client.post(
        "/members/new",
        data={"name": "가" * 51, "active": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(FamilyMember).count() == 0


def test_member_name_at_limit_accepted(client) -> None:
    """정확히 50자는 허용된다 (audit #77)."""
    test_client, Session = client
    resp = test_client.post(
        "/members/new",
        data={"name": "가" * 50, "active": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        assert s.query(FamilyMember).filter(FamilyMember.name == "가" * 50).count() == 1


def test_member_active_false_string_deactivates(client) -> None:
    """active="false" 문자열이 bool("false")=True 버그 없이 비활성 저장된다 (audit #78)."""
    test_client, Session = client
    resp = test_client.post(
        "/members/new",
        data={"name": "체크박스멤버", "active": "false"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        member = s.query(FamilyMember).filter(FamilyMember.name == "체크박스멤버").one()
        assert member.active is False


def test_duplicate_telegram_user_id_returns_400(client) -> None:
    """이미 등록된 telegram_user_id 재사용은 500이 아니라 400 (audit #47)."""
    test_client, Session = client
    r1 = test_client.post(
        "/members/new",
        data={"name": "엄마", "telegram_user_id": "100", "active": "on"},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    r2 = test_client.post(
        "/members/new",
        data={"name": "아빠", "telegram_user_id": "100", "active": "on"},
        follow_redirects=False,
    )
    assert r2.status_code == 400
    with Session() as s:
        assert s.query(FamilyMember).count() == 1


def test_lunar_feb30_returns_400_not_500(client) -> None:
    """음력 2월 30일 생일 입력은 500이 아니라 400으로 안내 (audit #45)."""
    test_client, Session = client
    resp = test_client.post(
        "/members/new",
        data={
            "name": "음력이",
            "birthday_lunar_month": "2",
            "birthday_lunar_day": "30",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(FamilyMember).count() == 0


def test_lunar_valid_30day_month_saved(client) -> None:
    """음력 4월 30일(30일까지 있는 달)은 정상 저장된다 (audit #45)."""
    test_client, Session = client
    resp = test_client.post(
        "/members/new",
        data={
            "name": "음력사월",
            "birthday_lunar_month": "4",
            "birthday_lunar_day": "30",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        m = s.query(FamilyMember).filter(FamilyMember.name == "음력사월").one()
        assert m.birthday_lunar == date(2000, 4, 30)


def test_edit_missing_member_returns_404(client) -> None:
    """없는 구성원 편집 폼/저장은 404 (audit #61)."""
    test_client, _ = client
    assert test_client.get("/members/999/edit").status_code == 404
    resp = test_client.post("/members/999/edit", data={"name": "없음"}, follow_redirects=False)
    assert resp.status_code == 404


# ── rules (#18, #58, #59, #61, #77, #78) ──────────────────────────


def test_bad_rule_hour_returns_400(client) -> None:
    """hour 범위 밖(25)은 500이 아니라 400 (audit #18/#59)."""
    test_client, Session = client
    resp = test_client.post(
        "/rules/new",
        data={
            "type": "birthday",
            "title": "생일",
            "birthday_member_id": "1",
            "birthday_hour": "25",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(ReminderRule).count() == 0


def test_unknown_rule_type_returns_400(client) -> None:
    """알 수 없는 규칙 type은 500이 아니라 400 (audit #59)."""
    test_client, _ = client
    resp = test_client.post(
        "/rules/new", data={"type": "xyz", "title": "x"}, follow_redirects=False
    )
    assert resp.status_code == 400


def test_bad_run_at_returns_400(client) -> None:
    """비ISO run_at은 500이 아니라 400 (audit #18)."""
    test_client, Session = client
    resp = test_client.post(
        "/rules/new",
        data={
            "type": "custom",
            "title": "공지",
            "custom_repeat": "once",
            "custom_message": "안녕",
            "custom_run_at": "2026/07/15 09시",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(ReminderRule).count() == 0


def test_edit_missing_rule_returns_404(client) -> None:
    """없는 규칙 편집 폼은 404 (audit #61)."""
    test_client, _ = client
    assert test_client.get("/rules/999/edit").status_code == 404


def test_rule_title_too_long_returns_400(client) -> None:
    """200자(ReminderRule.title 컬럼 길이)를 넘는 규칙 이름은 500 대신 400 (audit #77)."""
    test_client, Session = client
    resp = test_client.post(
        "/rules/new",
        data={"type": "custom", "title": "x" * 201},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(ReminderRule).count() == 0


def test_rule_active_false_string_deactivates(client) -> None:
    """규칙 active="false" 문자열이 bool() 오판정 없이 비활성으로 저장된다 (audit #78)."""
    test_client, Session = client
    resp = test_client.post(
        "/rules/new",
        data={
            "type": "custom",
            "title": "체크박스규칙",
            "active": "false",
            "custom_repeat": "once",
            "custom_message": "안녕",
            "custom_run_at": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        rule = s.query(ReminderRule).filter(ReminderRule.title == "체크박스규칙").one()
        assert rule.active is False


def test_parse_int_list_skips_invalid_tokens() -> None:
    """isdigit()를 통과하던 '--3'/'²' 등이 crash 없이 무시된다 (audit #58)."""
    assert _parse_int_list("7,--3,²,3, ,abc") == [7, 3]
    assert _parse_int_list("14,7,-3,1,0") == [14, 7, -3, 1, 0]


# ── health checks (#25, #46, #61, #77, #78) ──────────────────────


def _make_member(Session, name: str = "검진이", gender: str | None = None) -> int:
    with Session() as s:
        m = FamilyMember(name=name, gender=gender, active=True)
        s.add(m)
        s.commit()
        return m.id


def _make_type(Session, name: str = "위내시경", period_years: int = 2) -> int:
    with Session() as s:
        ct = HealthCheckType(name=name, period_years=period_years, active=True)
        s.add(ct)
        s.commit()
        return ct.id


def test_records_page_survives_feb29_record(client) -> None:
    """2/29 검진 기록 + 비윤년 예정일이어도 페이지가 500 없이 렌더된다 (audit #25)."""
    test_client, Session = client
    member_id = _make_member(Session)
    type_id = _make_type(Session, period_years=2)
    with Session() as s:
        s.add(
            HealthCheckRecord(
                member_id=member_id, check_type_id=type_id, checked_at=date(2024, 2, 29)
            )
        )
        s.commit()

    resp = test_client.get(f"/health/records/{member_id}")
    assert resp.status_code == 200
    # 2024 + 2 = 2026(비윤년) → 2/28로 폴백
    assert "2026-02-28" in resp.text


def test_duplicate_health_record_returns_400(client) -> None:
    """같은 (구성원,항목,검진일) 재제출은 500이 아니라 400 (audit #46)."""
    test_client, Session = client
    member_id = _make_member(Session)
    type_id = _make_type(Session)
    payload = {"check_type_id": str(type_id), "checked_at": "2024-06-01"}
    r1 = test_client.post(f"/health/records/{member_id}/add", data=payload, follow_redirects=False)
    assert r1.status_code == 303
    r2 = test_client.post(f"/health/records/{member_id}/add", data=payload, follow_redirects=False)
    assert r2.status_code == 400


def test_bad_checked_at_returns_400(client) -> None:
    """비정상 checked_at 문자열은 500이 아니라 400 (audit #46)."""
    test_client, Session = client
    member_id = _make_member(Session)
    type_id = _make_type(Session)
    resp = test_client.post(
        f"/health/records/{member_id}/add",
        data={"check_type_id": str(type_id), "checked_at": "어제"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_duplicate_type_name_returns_400(client) -> None:
    """중복 검진 항목명은 500이 아니라 400 (audit #46)."""
    test_client, _ = client
    data = {"name": "심전도", "period_years": "1"}
    r1 = test_client.post("/health/types/new", data=data, follow_redirects=False)
    assert r1.status_code == 303
    r2 = test_client.post("/health/types/new", data=data, follow_redirects=False)
    assert r2.status_code == 400


def test_missing_member_records_returns_404(client) -> None:
    """없는 구성원 검진 페이지는 404 (audit #61)."""
    test_client, _ = client
    assert test_client.get("/health/records/999").status_code == 404


def test_update_missing_type_returns_404(client) -> None:
    """없는 검진 항목 수정은 404 (audit #61)."""
    test_client, _ = client
    resp = test_client.post(
        "/health/types/999/edit",
        data={"name": "없음", "period_years": "2"},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_health_check_type_name_too_long_returns_400(client) -> None:
    """100자(HealthCheckType.name 컬럼 길이)를 넘는 항목명은 500 대신 400 (audit #77)."""
    test_client, Session = client
    resp = test_client.post(
        "/health/types/new",
        data={"name": "x" * 101, "period_years": "2"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(HealthCheckType).count() == 0


def test_health_check_type_name_at_limit_accepted(client) -> None:
    """정확히 100자는 허용된다 — name<=50 일괄 적용이 아니라 실제 컬럼 길이(100)를
    기준으로 삼았음을 고정하는 회귀 테스트다 (audit #77)."""
    test_client, Session = client
    resp = test_client.post(
        "/health/types/new",
        data={"name": "x" * 100, "period_years": "2"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        assert s.query(HealthCheckType).filter(HealthCheckType.name == "x" * 100).count() == 1


def test_health_check_type_period_years_out_of_range_returns_400(client) -> None:
    """검진 주기가 1~50 범위를 벗어나면(0 이하/비정상적으로 큼) 500 대신 400 (audit #77)."""
    test_client, Session = client
    resp = test_client.post(
        "/health/types/new",
        data={"name": "범위밖검진", "period_years": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    resp2 = test_client.post(
        "/health/types/new",
        data={"name": "범위밖검진2", "period_years": "999999999"},
        follow_redirects=False,
    )
    assert resp2.status_code == 400
    with Session() as s:
        assert s.query(HealthCheckType).count() == 0


def test_health_check_type_active_false_string_deactivates(client) -> None:
    """검진 항목 active="false" 문자열이 bool() 오판정 없이 비활성 저장된다 (audit #78)."""
    test_client, Session = client
    resp = test_client.post(
        "/health/types/new",
        data={"name": "체크박스검진", "period_years": "2", "active": "false"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        ct = s.query(HealthCheckType).filter(HealthCheckType.name == "체크박스검진").one()
        assert ct.active is False


def test_member_health_config_period_years_out_of_range_returns_400(client) -> None:
    """구성원별 검진 주기 오버라이드도 1~50 범위를 벗어나면 500 대신 400 (audit #77)."""
    test_client, Session = client
    member_id = _make_member(Session)
    type_id = _make_type(Session)
    resp = test_client.post(
        f"/health/members/{member_id}/config/{type_id}",
        data={"period_years": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(MemberHealthCheckConfig).count() == 0


def test_member_health_config_active_false_string_deactivates(client) -> None:
    """구성원별 검진 알림 active="false"가 bool() 오판정 없이 꺼진다 (audit #78)."""
    test_client, Session = client
    member_id = _make_member(Session)
    type_id = _make_type(Session)
    resp = test_client.post(
        f"/health/members/{member_id}/config/{type_id}",
        data={"active": "false"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        cfg = (
            s.query(MemberHealthCheckConfig)
            .filter(
                MemberHealthCheckConfig.member_id == member_id,
                MemberHealthCheckConfig.check_type_id == type_id,
            )
            .one()
        )
        assert cfg.active is False


# ── CSRF (#37) ───────────────────────────────────────────────────


def test_csrf_cross_site_post_blocked(client) -> None:
    """cross-site Sec-Fetch-Site POST는 403으로 차단된다 (audit #37)."""
    test_client, Session = client
    resp = test_client.post(
        "/broadcast",
        data={"message": "악성공지"},
        headers={"Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    with Session() as s:
        assert s.query(ScheduledNotification).count() == 0


def test_csrf_same_origin_post_allowed(client) -> None:
    """same-origin Sec-Fetch-Site POST는 통과한다 (audit #37)."""
    test_client, _ = client
    resp = test_client.post(
        "/broadcast",
        data={"message": "정상공지"},
        headers={"Sec-Fetch-Site": "same-origin"},
        follow_redirects=False,
    )
    assert resp.status_code == 200


def test_health_check_rule_type_rejected(client) -> None:
    """건강검진은 규칙으로 저장할 수 없다(전용 생성기가 자동 발송) — 400, 미저장 (audit #22)."""
    test_client, Session = client
    resp = test_client.post(
        "/rules/new",
        data={
            "type": "health_check",
            "title": "건강검진 규칙",
            "health_member_id": "1",
            "health_anchor_date": "2026-01-10",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(ReminderRule).count() == 0


# ── broadcast (#79) ────────────────────────────────────────────────


def test_broadcast_message_too_long_returns_400(client) -> None:
    """4096자(텔레그램 메시지 한도)를 넘는 공지는 500 대신 400 (audit #79)."""
    test_client, Session = client
    resp = test_client.post(
        "/broadcast",
        data={"message": "x" * 4097},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    with Session() as s:
        assert s.query(ScheduledNotification).count() == 0
        assert s.query(AdminBroadcast).count() == 0


def test_broadcast_message_at_limit_accepted(client) -> None:
    """정확히 4096자는 허용된다 (audit #79)."""
    test_client, _ = client
    resp = test_client.post(
        "/broadcast",
        data={"message": "x" * 4096},
        follow_redirects=False,
    )
    assert resp.status_code == 200
