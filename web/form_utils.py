"""폼 입력 파싱 안전 헬퍼.

라우트가 int()/date.fromisoformat()를 무방비로 호출해 비정상 입력에서
ValueError→500이 나던 문제를 막는다. 잘못된 값은 500이 아니라 400으로 처리한다
(audit #18, #45, #46, #47, #58, #59).
"""

from datetime import date, datetime

from fastapi import HTTPException


def parse_int_default(value: str | None, label: str, default: int) -> int:
    """빈 값이면 default, 아니면 int. 정수가 아니면 400."""
    s = (value or "").strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label}은(는) 숫자로 입력해주세요.") from None


def parse_optional_int(value: str | None, label: str) -> int | None:
    """빈 값이면 None, 아니면 int. 정수가 아니면 400."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label}은(는) 숫자로 입력해주세요.") from None


def require_range(value: int, label: str, lo: int, hi: int) -> int:
    """정수 범위 검증. 벗어나면 400."""
    if not (lo <= value <= hi):
        raise HTTPException(status_code=400, detail=f"{label}은(는) {lo}~{hi} 사이여야 합니다.")
    return value


def parse_optional_date(value: str | None, label: str) -> date | None:
    """빈 값이면 None, 아니면 date. 형식 오류면 400."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} 형식이 올바르지 않습니다.") from None


def parse_required_date(value: str | None, label: str) -> date:
    """date 필수. 형식 오류/누락이면 400."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} 형식이 올바르지 않습니다.") from None


def validate_iso_datetime(value: str | None, label: str) -> str:
    """ISO datetime 문자열이면 원문 그대로 반환(저장은 문자열 유지), 아니면 400.

    빈 값은 빈 문자열로 통과시킨다(예약 일시 미입력 허용).
    """
    s = (value or "").strip()
    if not s:
        return ""
    try:
        datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} 형식이 올바르지 않습니다.") from None
    return s
