from shared.lunar import lunar_to_solar


def test_lunar_to_solar_valid_date() -> None:
    # 음력 2026-08-06 → 양력 2026-09-16
    assert lunar_to_solar(2026, 8, 6) == (2026, 9, 16)


def test_lunar_to_solar_nonexistent_date_returns_none() -> None:
    """존재하지 않는 음력 날짜(음력 2026-04-30 등)는 (0,0,0)이 아니라 None (audit #0)."""
    # 라이브러리가 False/'0000-00-00'을 돌려주는 케이스
    assert lunar_to_solar(2026, 4, 30) is None
