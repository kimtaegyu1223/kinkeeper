"""배포 스크립트/유닛 파일 정합성 회귀 테스트 (audit #20, #21, #49, #50, #72, #73)."""

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
