"""ReminderRule.config JSONB 스키마의 코드 문서화.

config JSONB는 규칙 type마다 형태가 다르다. 여기 TypedDict가 그 형태의 단일 출처(문서)다 —
전에는 생성기·rules.py·템플릿 3~4곳을 교차 확인해야 형태를 알 수 있었다 (audit #25).

런타임 검증은 하지 않는다(관리자 1명이 웹 폼으로만 작성, 임의 JSON 주입 경로 없음). 생성기는
여전히 .get(key, default)로 안전하게 읽으므로 키가 없어도 동작한다 — total=False로 '모든 키가
있을 수도/없을 수도 있음'을 표현한다. 생성기·rules.py가 rule.config를 이 TypedDict로 다루면
mypy가 키 오타를 잡아준다(런타임 비용 0).
"""

from typing import TypedDict


class BirthdayConfig(TypedDict, total=False):
    """type == "birthday" 규칙의 config."""

    member_id: int  # 대상 구성원 id (필수 — 없으면 생성기가 조기 반환)
    use_lunar: bool  # 음력 생일 여부 (기본 False)
    hour: int  # 발송 시각(시, 0-23, 기본 9)


class HolidayConfig(TypedDict, total=False):
    """type == "holiday" 규칙의 config."""

    name: str  # 명절/기일 이름 (기본 "명절")
    lunar_month: int  # 음력 월 (필수 — 없으면 조기 반환)
    lunar_day: int  # 음력 일 (필수 — 없으면 조기 반환)
    hour: int  # 발송 시각(시, 기본 9)


class CustomConfig(TypedDict, total=False):
    """type == "custom" 규칙의 config.

    repeat == "yearly"면 매년 반복(month/day/use_lunar 사용), 그 외/없으면 1회성(run_at 사용).
    """

    message: str  # 발송 문구 (없으면 rule.title 사용)
    hour: int  # 발송 시각(시, 기본 9)
    repeat: str  # "yearly" | "once"(기본)
    # repeat == "yearly" 전용
    month: int
    day: int
    use_lunar: bool
    # 1회성(once) 전용
    run_at: str  # ISO datetime 문자열 (KST naive면 생성기가 UTC로 변환)
