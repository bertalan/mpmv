"""Release and rollback by image tag on a STAGE/LIVE host (ARC.5, ADR-0005).

The deploy script drives `podman` and `systemctl --user`. Here both are replaced by fakes on PATH
that log their arguments, so the release logic is checked without a server.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "server" / "mpmv-deploy"
REGISTRY = "registry.invalid/acme/mpmv-app"

FAKE_PODMAN = """#!/bin/sh
echo "podman $*" >> "$FAKE_DIR/log"
case "$1" in
  tag) echo "${2##*:}" > "$FAKE_DIR/current" ;;
  healthcheck)
    current=$(cat "$FAKE_DIR/current" 2>/dev/null || true)
    if [ -f "$FAKE_DIR/bad" ] && grep -qxF "$current" "$FAKE_DIR/bad"; then exit 1; fi ;;
esac
exit 0
"""

FAKE_SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$FAKE_DIR/log"
case "$*" in
  *mpmv-migrate.service*) [ -f "$FAKE_DIR/migrate-fails" ] && exit 1 ;;
  *" restart "*)
    current=$(cat "$FAKE_DIR/current" 2>/dev/null || true)
    if [ -f "$FAKE_DIR/restart-fails" ] && grep -qxF "$current" "$FAKE_DIR/restart-fails"; then
      exit 1
    fi ;;
esac
exit 0
"""


@dataclass
class Host:
    root: Path

    @property
    def log(self) -> list[str]:
        path = self.root / "log"
        return path.read_text().splitlines() if path.exists() else []

    @property
    def releases(self) -> list[str]:
        path = self.root / "state" / "releases"
        return path.read_text().split() if path.exists() else []

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        (self.root / "log").unlink(missing_ok=True)
        env = {
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "FAKE_DIR": str(self.root),
            "MPMV_IMAGE": REGISTRY,
            "MPMV_STATE_DIR": str(self.root / "state"),
            "MPMV_HEALTH_RETRIES": "2",
            "MPMV_HEALTH_INTERVAL": "0",
        }
        return subprocess.run(  # noqa: S603 (fixed script path, test-controlled arguments)
            ["/bin/sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
        )


@pytest.fixture
def host(tmp_path: Path) -> Host:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("podman", FAKE_PODMAN), ("systemctl", FAKE_SYSTEMCTL)):
        fake = bin_dir / name
        fake.write_text(body)
        fake.chmod(0o755)
    return Host(tmp_path)


def test_deploy_pulls_migrates_then_restarts(host: Host) -> None:
    result = host.run("deploy", "1.2.0")
    assert result.returncode == 0, result.stderr
    assert host.log[:5] == [
        f"podman pull --policy=missing {REGISTRY}:1.2.0",
        f"podman tag {REGISTRY}:1.2.0 localhost/mpmv-app:current",
        "systemctl --user start mpmv-migrate.service",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
        "podman healthcheck run mpmv-app",
    ]
    assert host.releases == ["1.2.0"]


def test_rollback_switches_to_the_previous_tag_without_migrating(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    assert host.run("deploy", "1.1.0").returncode == 0

    result = host.run("rollback")

    assert result.returncode == 0, result.stderr
    assert f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current" in host.log
    assert not any("mpmv-migrate" in line for line in host.log)
    assert host.releases == ["1.0.0"]


def test_rollback_needs_a_previous_release(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0

    result = host.run("rollback")

    assert result.returncode != 0
    assert not any(line.startswith("podman tag") for line in host.log)
    assert host.releases == ["1.0.0"]


def test_unhealthy_release_is_rolled_back_automatically(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "bad").write_text("1.1.0\n")

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert host.log[-3:] == [
        f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
        "podman healthcheck run mpmv-app",
    ]
    assert host.releases == ["1.0.0"]


def test_failed_restart_is_rolled_back_like_an_unhealthy_release(host: Host) -> None:
    # A unit that cannot start (bad image config, missing env file, start timeout) must not leave
    # the new tag in place with the services restarting it forever.
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "restart-fails").write_text("1.1.0\n")

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert "failed" in result.stderr
    assert host.log[-3:] == [
        f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
        "podman healthcheck run mpmv-app",
    ]
    assert (host.root / "current").read_text().strip() == "1.0.0"
    assert host.releases == ["1.0.0"]


def test_failed_restart_during_auto_rollback_still_reports(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "restart-fails").write_text("1.0.0\n1.1.0\n")

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert "release 1.0.0 is unhealthy too" in result.stderr
    assert "release 1.1.0 failed" in result.stderr
    assert host.releases == ["1.0.0"]


def test_failed_restart_during_rollback_returns_to_the_live_release(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    assert host.run("deploy", "1.1.0").returncode == 0
    (host.root / "restart-fails").write_text("1.0.0\n")

    result = host.run("rollback")

    assert result.returncode != 0
    assert "release 1.0.0 is unhealthy" in result.stderr
    assert host.log[-2:] == [
        f"podman tag {REGISTRY}:1.1.0 localhost/mpmv-app:current",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
    ]
    assert (host.root / "current").read_text().strip() == "1.1.0"
    assert host.releases == ["1.0.0", "1.1.0"]


def test_failed_migration_restores_the_current_tag_and_keeps_services(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "migrate-fails").touch()

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert host.log[-1] == f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current"
    assert not any("restart" in line for line in host.log)
    assert host.releases == ["1.0.0"]


@pytest.mark.parametrize("version", ["latest", "", "../1.0", "1.0;rm"])
def test_deploy_rejects_mutable_or_malformed_tags(host: Host, version: str) -> None:
    result = host.run("deploy", version)
    assert result.returncode != 0
    assert host.log == []


def test_status_prints_current_and_previous(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    assert host.run("deploy", "1.1.0").returncode == 0

    result = host.run("status")

    assert result.returncode == 0
    assert "current: 1.1.0" in result.stdout
    assert "previous: 1.0.0" in result.stdout
