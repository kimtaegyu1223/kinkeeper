"""배포 스크립트/유닛 파일 정합성 회귀 테스트 (audit #20, #21, #49, #50, #72, #73)."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

UNIT_NAMES = ("kinkeeper-bot", "kinkeeper-web", "kinkeeper-web-tailscale")


def test_tailscale_unit_exists() -> None:
    """deploy.sh가 재시작하는 kinkeeper-web-tailscale 유닛이 저장소에 존재해야 함 (audit #20)."""
    assert (DEPLOY / "kinkeeper-web-tailscale.service").is_file()


def test_units_are_user_units() -> None:
    """install.sh/deploy.sh가 --user 유닛을 쓰므로 유닛도 user 유닛이어야 함 (audit #20)."""
    for name in UNIT_NAMES:
        lines = [ln.strip() for ln in (DEPLOY / f"{name}.service").read_text().splitlines()]
        assert "WantedBy=default.target" in lines
        assert "WantedBy=multi-user.target" not in lines
        # user 유닛은 User= 를 지정할 수 없다.
        assert not any(ln.startswith("User=") for ln in lines)


def test_install_uses_user_systemd_and_linger() -> None:
    """install.sh가 systemctl --user + enable-linger를 쓰고 system 유닛은 안 써야 함 (audit #20)."""
    text = (DEPLOY / "install.sh").read_text()
    assert "systemctl --user" in text
    assert "enable-linger" in text
    assert "/etc/systemd/system" not in text


def test_install_handles_docker_group() -> None:
    """docker 신규 설치 시 그룹 미적용 문제를 sg docker로 처리해야 함 (audit #21)."""
    assert "sg docker" in (DEPLOY / "install.sh").read_text()


def test_install_waits_with_pg_isready() -> None:
    """install.sh가 고정 sleep 대신 pg_isready 폴링으로 DB를 대기해야 함 (audit #50)."""
    assert "pg_isready" in (DEPLOY / "install.sh").read_text()


def test_install_guidance_uses_loopback() -> None:
    """설치 안내가 LAN IP(hostname -I) 대신 127.0.0.1을 안내해야 함 (audit #73)."""
    text = (DEPLOY / "install.sh").read_text()
    assert "127.0.0.1:8000" in text
    assert "hostname -I" not in text


def test_readme_guidance_uses_loopback() -> None:
    """README 안내도 127.0.0.1 기준이어야 함 (audit #73)."""
    assert "http://서버IP:8000" not in (ROOT / "README.md").read_text()


def test_pg_backup_discovers_container() -> None:
    """pg_backup.sh가 하드코딩 대신 compose로 컨테이너를 탐색해야 함 (audit #49)."""
    text = (DEPLOY / "pg_backup.sh").read_text()
    assert "docker compose ps -q db" in text
    assert "kinkeeper-db-1" not in text


def test_pg_backup_atomic_write() -> None:
    """pg_dump 실패 시 부분 파일이 남지 않도록 임시파일+mv를 써야 함 (audit #72)."""
    text = (DEPLOY / "pg_backup.sh").read_text()
    assert ".tmp" in text
    assert "mv " in text


def test_deploy_stops_before_migrate_and_starts_after() -> None:
    """파괴적 마이그레이션 대비 deploy.sh는 stop→migrate→start 순서여야 한다.

    restart를 migrate 뒤에 두면 컬럼 드롭 직후~재시작 전까지 구코드가 드롭된 컬럼을
    조회해 500/스케줄러 예외를 낼 수 있다(expand/contract 위반). 서비스 정지가
    마이그레이션보다, 마이그레이션이 서비스 시작보다 앞서는지 확인한다.
    """
    text = (DEPLOY / "deploy.sh").read_text()
    stop_at = text.find("systemctl --user stop")
    migrate_at = text.find("alembic upgrade head")
    start_at = text.find("systemctl --user start")

    assert stop_at != -1, "서비스 정지 단계가 없음"
    assert start_at != -1, "서비스 시작 단계가 없음"
    assert migrate_at != -1, "마이그레이션 단계가 없음"
    assert stop_at < migrate_at < start_at, "stop→migrate→start 순서가 아님"
    # restart는 stop→start 창을 없애므로 파괴적 마이그레이션 배포에서 쓰면 안 된다.
    assert "systemctl --user restart" not in text


def test_healthz_alert_exists_and_executable() -> None:
    """healthz cron 경보 스크립트가 존재하고 실행 권한이 있어야 한다 (2026-07-11 결정)."""
    script = DEPLOY / "healthz_alert.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK), "healthz_alert.sh에 실행 권한이 없음"


def test_healthz_alert_syntax_ok() -> None:
    """bash -n 구문 검사를 통과해야 한다."""
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY / "healthz_alert.sh")],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_healthz_alert_locks_key_behavior() -> None:
    """경보 스크립트의 핵심 동작(대상 URL·전송·자격증명·쿨다운·복구)을 잠근다."""
    text = (DEPLOY / "healthz_alert.sh").read_text()
    assert "set -euo pipefail" in text
    assert "127.0.0.1:8000/healthz" in text
    assert "sendMessage" in text  # 텔레그램 Bot API로 발송
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "GROUP_CHAT_ID" in text
    assert "COOLDOWN=3600" in text  # 같은 장애당 1시간 쿨다운
    assert "복구됨" in text  # 복구 시 1회 알림


def test_failed_alert_exists_and_executable() -> None:
    """발송 실패 감시 cron 경보 스크립트가 존재하고 실행 권한이 있어야 한다 (2026-07-11 결정)."""
    script = DEPLOY / "failed_alert.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK), "failed_alert.sh에 실행 권한이 없음"


def test_failed_alert_syntax_ok() -> None:
    """bash -n 구문 검사를 통과해야 한다."""
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY / "failed_alert.sh")],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_failed_alert_locks_key_behavior() -> None:
    """발송 실패 경보의 핵심 동작(컨테이너 탐색·조회·전송·자격증명·상태 게이팅)을 잠근다."""
    text = (DEPLOY / "failed_alert.sh").read_text()
    assert "set -euo pipefail" in text
    # pg_backup.sh와 동일하게 compose로 db 컨테이너를 탐색(하드코딩 금지).
    assert "docker compose ps -q db" in text
    assert "kinkeeper-db-1" not in text
    # 최근 24시간 내 failed 조회.
    assert "status='failed'" in text
    assert "24 hours" in text
    # 텔레그램 Bot API로 발송 + 자격증명.
    assert "sendMessage" in text
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "GROUP_CHAT_ID" in text
    # parse_mode 없이 평문 발송(<,& 등 HTML escape 불필요) — parse_mode 인자를 안 보냄.
    assert "parse_mode=" not in text
    # 새 실패(id > last_notified_id)일 때만 경보하는 상태 게이팅.
    assert "LAST_NOTIFIED_ID" in text
    assert "failed_alert.state" in text
