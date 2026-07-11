"""/healthz 상태코드·프로브 캐시 회귀 테스트 (audit #69 및 후속).

- 상태코드: DB 장애 시 body만 바꾸고 200을 주면 상태코드 기반 모니터가 장애를
  놓친다. DB 정상이면 200, 장애면 503을 반환해야 한다.
- 프로브 캐시: 연타 시 매번 DB 왕복하면 커넥션 풀(15개)이 마를 수 있어, 프로브
  결과를 몇 초 캐시한다. 연속 호출은 프로브 1회, TTL 만료 후엔 재프로브,
  동시 도착(thundering herd)에도 프로브 1회여야 한다.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

import web.main as web_main
from web.main import app


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """테스트 간 모듈 전역 프로브 캐시가 새지 않게 매 테스트 앞뒤로 비운다.

    app이 모듈 전역이라 캐시도 프로세스 전체에서 공유된다. 리셋이 없으면 앞선
    테스트의 캐시(예: ok=True)가 다음 테스트(db_down)로 새어 상태코드가 뒤집힌다.
    """
    web_main._healthz_probe_cache = None
    yield
    web_main._healthz_probe_cache = None


def test_healthz_ok_returns_200(monkeypatch) -> None:
    monkeypatch.setattr(web_main, "check_db_connection", lambda: True)
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


def test_healthz_db_down_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(web_main, "check_db_connection", lambda: False)
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "db_error", "db": "error"}


def test_healthz_caches_db_probe(monkeypatch) -> None:
    """연속 호출 시 DB 프로브는 캐시로 1회만 나가야 한다(커넥션 풀 보호)."""
    calls = {"n": 0}

    def counting_probe() -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr(web_main, "check_db_connection", counting_probe)
    client = TestClient(app)
    for _ in range(5):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "db": "ok"}
    assert calls["n"] == 1, "연속 호출인데 DB 프로브가 여러 번 나감(캐시 미동작)"


def test_healthz_reprobes_after_ttl_expiry(monkeypatch) -> None:
    """TTL이 지나면 다시 프로브해야 한다(스테일 경보 지연은 몇 초로 제한)."""
    calls = {"n": 0}

    def counting_probe() -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr(web_main, "check_db_connection", counting_probe)
    client = TestClient(app)
    client.get("/healthz")
    assert calls["n"] == 1
    # 캐시 만료 시각을 과거로 밀어 만료를 시뮬레이션(sleep 없이 결정적으로).
    assert web_main._healthz_probe_cache is not None
    web_main._healthz_probe_cache = (0.0, True)
    client.get("/healthz")
    assert calls["n"] == 2, "TTL 만료 후에도 재프로브하지 않음"


def test_probe_single_flight_under_concurrency(monkeypatch) -> None:
    """캐시 만료 상태에서 여러 스레드가 동시에 도착해도 프로브는 1회여야 한다."""
    calls = {"n": 0}
    n_threads = 8
    start = threading.Barrier(n_threads)

    def slow_probe() -> bool:
        calls["n"] += 1  # 단일 비행 보장 시 락 안에서만 실행되어 경합 없음
        time.sleep(0.05)  # 프로브가 도는 동안 나머지 스레드가 락에 쌓이게 한다
        return True

    monkeypatch.setattr(web_main, "check_db_connection", slow_probe)
    web_main._healthz_probe_cache = None
    results: list[bool] = []

    def worker() -> None:
        start.wait()  # 모든 스레드를 동시에 출발시킨다
        results.append(web_main._probe_db_cached())

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls["n"] == 1, "thundering herd — 동시 도착 시 중복 프로브가 나감"
    assert results == [True] * n_threads
