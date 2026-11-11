"""Release and rollback by image tag on a STAGE/LIVE host (ARC.5, ARC.23, ADR-0005).

The deploy script drives `podman`, `systemctl --user`, Podman's quadlet generator and `curl`. Here
each is a fake on PATH that logs its arguments, and every release image is a directory holding the
`/app/deploy` tree the real image carries, so the release logic runs without a server.
"""

import hashlib
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCRIPT = DEPLOY / "server" / "mpmv-deploy"
REGISTRY = "registry.invalid/acme/mpmv-app"
SITE = "site.invalid"
MEDIA_FILE = "/app/media/original_images/demo.webp"

FAKE_PODMAN = r"""#!/bin/sh
case "$*" in
  *--help*) grep -qxF "$1 $2" "$FAKE_DIR/podman-lacks" 2>/dev/null && exit 125; exit 0 ;;
esac
echo "podman $*" >> "$FAKE_DIR/log"
current=$(cat "$FAKE_DIR/current" 2>/dev/null || true)
case "$1" in
  image) [ "$2" = exists ] && grep -qxF "$3" "$FAKE_DIR/local" 2>/dev/null; exit ;;
  pull) echo "$2" >> "$FAKE_DIR/local" ;;
  tag) echo "${2##*:}" > "$FAKE_DIR/current" ;;
  create) echo "ctr-${2##*:}" ;;
  cp)
    tag="${2%%:*}"; tag="${tag#ctr-}"; part="${2##*/}"
    [ -d "$FAKE_DIR/images/$tag/$part" ] || exit 125
    cp -R "$FAKE_DIR/images/$tag/$part" "$3" ;;
  healthcheck)
    if [ -f "$FAKE_DIR/bad" ] && grep -qxF "$current" "$FAKE_DIR/bad"; then exit 1; fi ;;
  exec) [ -f "$FAKE_DIR/media" ] && cat "$FAKE_DIR/media" ;;
  volume)
    case "$2" in
      exists) [ -f "$FAKE_DIR/volume-owner" ]; exit ;;
      inspect) echo "$FAKE_DIR/volume" ;;
    esac ;;
  unshare) cat "$FAKE_DIR/volume-owner" ;;
  info) echo "$FAKE_DIR" ;;
esac
exit 0
"""

FAKE_SYSTEMCTL = r"""#!/bin/sh
echo "systemctl $*" >> "$FAKE_DIR/log"
current=$(cat "$FAKE_DIR/current" 2>/dev/null || true)
case "$*" in
  *mpmv-migrate.service*) [ -f "$FAKE_DIR/migrate-fails" ] && exit 1 ;;
  *" is-active "*) [ -f "$FAKE_DIR/caddy-active" ]; exit ;;
  *" start mpmv-caddy.service"*) touch "$FAKE_DIR/caddy-active" ;;
  *" stop mpmv-caddy.service"*) rm -f "$FAKE_DIR/caddy-active" ;;
  *" restart mpmv-caddy.socket"*)
    # The ports are still held by a proxy that publishes them itself.
    [ -f "$FAKE_DIR/caddy-active" ] && exit 1
    [ -f "$FAKE_DIR/socket-fails" ] && exit 1
    touch "$FAKE_DIR/socket-active" ;;
  *" stop mpmv-caddy.socket"*) rm -f "$FAKE_DIR/socket-active" ;;
  *" show -p NRestarts "*)
    n=$(cat "$FAKE_DIR/restarts" 2>/dev/null || echo 0)
    if [ -f "$FAKE_DIR/crashes" ] && grep -qxF "$current" "$FAKE_DIR/crashes"; then
      n=$((n + 1)); echo "$n" > "$FAKE_DIR/restarts"
    fi
    echo "$n" ;;
  *" restart "*)
    if [ -f "$FAKE_DIR/restart-fails" ] && grep -qxF "$current" "$FAKE_DIR/restart-fails"; then
      exit 1
    fi ;;
esac
exit 0
"""

FAKE_CURL = r"""#!/bin/sh
echo "curl $*" >> "$FAKE_DIR/log"
for url; do :; done
current=$(cat "$FAKE_DIR/current" 2>/dev/null || true)
if [ -f "$FAKE_DIR/http-bad" ] && grep -qxF "$current" "$FAKE_DIR/http-bad"; then
  printf 500; exit 0
fi
printf 200
"""

# Podman's generator, reduced to what the script relies on: one service per unit, or an error
# for a key the host's Podman does not know.
FAKE_QUADLET = r"""#!/bin/sh
[ "$1 $2" = "-dryrun -user" ] || exit 2
for f in "$QUADLET_UNIT_DIRS"/*; do
  [ -f "$f" ] || continue
  if grep -q '^UnknownToThisPodman=' "$f"; then echo "unsupported key in $f" >&2; exit 1; fi
  name="${f##*/}"
  case "$name" in
    *.container) echo "---${name%.container}.service---" ;;
    *.volume) echo "---${name%.volume}-volume.service---" ;;
    *.network) echo "---${name%.network}-network.service---" ;;
  esac
done
"""


# Caddy restarted on the socket unit: the old proxy releases the ports before the socket binds them.
PROXY_ON_SOCKET = [
    "systemctl --user stop mpmv-caddy.service",
    "systemctl --user restart mpmv-caddy.socket",
    "systemctl --user start mpmv-caddy.service",
]
# Caddy restarted as before the switch: it publishes the ports itself, the socket is released.
PROXY_PUBLISHING = [
    "systemctl --user stop mpmv-caddy.service",
    "systemctl --user stop mpmv-caddy.socket",
    "systemctl --user start mpmv-caddy.service",
]


def actions(log: list[str]) -> list[str]:
    """The log lines that change the host, in order (probes and checks left out)."""
    keep = (
        "podman pull",
        "podman tag",
        "systemctl --user daemon-reload",
        "systemctl --user enable",
        "systemctl --user disable",
        "systemctl --user start",
        "systemctl --user stop",
        "systemctl --user restart",
    )
    return [line for line in log if line.startswith(keep)]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Host:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / ".config" / "mpmv"

    @property
    def units(self) -> Path:
        return self.root / ".config" / "containers" / "systemd"

    @property
    def systemd(self) -> Path:
        return self.root / ".config" / "systemd" / "user"

    @property
    def script(self) -> Path:
        return self.root / ".local" / "bin" / "mpmv-deploy"

    @property
    def log(self) -> list[str]:
        path = self.root / "log"
        return path.read_text().splitlines() if path.exists() else []

    @property
    def releases(self) -> list[str]:
        path = self.root / "state" / "releases"
        return path.read_text().split() if path.exists() else []

    @property
    def live_tag(self) -> str:
        return (self.root / "current").read_text().strip()

    def release(
        self, tag: str, *, caddy: str = "", app_extra: str = "", published_ports: bool = False
    ) -> Path:
        """A release image: the repository's deploy tree, marked with its tag.

        `published_ports` builds a release from before the socket switch: no deploy/systemd, and a
        Caddy unit that publishes 80 and 443 itself.
        """
        image = self.root / "images" / tag
        for part in ("quadlet", "caddy", "server", "systemd"):
            shutil.copytree(DEPLOY / part, image / part)
        if published_ports:
            shutil.rmtree(image / "systemd")
            unit = image / "quadlet" / "mpmv-caddy.container"
            lines = [
                line
                for line in unit.read_text().splitlines()
                if not line.startswith(("Requires=", "After="))
            ]
            at = lines.index("Network=mpmv.network") + 1
            lines[at:at] = ["PublishPort=80:80", "PublishPort=443:443", "PublishPort=443:443/udp"]
            unit.write_text("\n".join(lines) + "\n")
        app = image / "quadlet" / "mpmv-app.container"
        app.write_text(f"{app.read_text()}# release {tag}\n{app_extra}")
        caddyfile = image / "caddy" / "Caddyfile"
        caddyfile.write_text(f"{caddyfile.read_text()}# proxy {caddy or 'default'}\n")
        return image

    def installed_release(self) -> str:
        last = (self.units / "mpmv-app.container").read_text().splitlines()[-1]
        return last.removeprefix("# release ")

    def run(
        self, *args: str, script: Path = SCRIPT, **extra: str
    ) -> subprocess.CompletedProcess[str]:
        (self.root / "log").unlink(missing_ok=True)
        # A host that may bind 80/443 unless a test says otherwise: never read the runner's own
        # /proc/sys (GitHub runners keep the kernel default 1024, a workstation has no /proc).
        default_proc = self.root / "proc-sys-default"
        (default_proc / "net" / "ipv4").mkdir(parents=True, exist_ok=True)
        (default_proc / "net" / "ipv4" / "ip_unprivileged_port_start").write_text("80\n")
        default_net = self.root / "proc-net-default"
        default_net.mkdir(exist_ok=True)
        (default_net / "if_inet6").write_text(
            "00000000000000000000000000000001 01 80 10 80       lo\n"
        )
        env = {
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "FAKE_DIR": str(self.root),
            "MPMV_IMAGE": REGISTRY,
            "MPMV_STATE_DIR": str(self.root / "state"),
            "MPMV_QUADLET": str(self.root / "bin" / "quadlet"),
            "MPMV_CHECK_BUDGET": "0",
            "MPMV_CHECK_INTERVAL": "0",
            "MPMV_MIN_FREE_MB": "1",
            "MPMV_PROC_SYS": str(default_proc),
            "MPMV_PROC_NET": str(default_net),
            **extra,
        }
        return subprocess.run(  # noqa: S603 (fixed script path, test-controlled arguments)
            ["/bin/sh", str(script), *args], env=env, capture_output=True, text=True, check=False
        )


def fill_env(example: Path, target: Path, values: dict[str, str]) -> None:
    lines = []
    for raw in example.read_text().splitlines():
        name, sep, default = raw.partition("=")
        if sep and not raw.startswith("#"):
            lines.append(f"{name}={values.get(name, default or secrets.token_urlsafe(12))}")
    target.write_text("\n".join(lines) + "\n")


@pytest.fixture
def host(tmp_path: Path) -> Host:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fakes = {
        "podman": FAKE_PODMAN,
        "systemctl": FAKE_SYSTEMCTL,
        "curl": FAKE_CURL,
        "quadlet": FAKE_QUADLET,
    }
    for name, body in fakes.items():
        fake = bin_dir / name
        fake.write_text(body)
        fake.chmod(0o755)
    server = Host(tmp_path)
    server.config.mkdir(parents=True)
    for name in ("mpmv", "database", "caddy"):
        fill_env(
            DEPLOY / "server" / f"{name}.env.example",
            server.config / f"{name}.env",
            {"MPMV_SITE_ADDRESS": SITE},
        )
    (server.config / "pg-root-ca.crt").write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    )
    (tmp_path / "volume-owner").write_text("10001:10001\n")
    for tag in ("1.0.0", "1.0.1", "1.1.0", "1.2.0"):
        server.release(tag)
    return server


# --- Deploy: units of the tag, then migrate, restart and check.


def test_deploy_installs_the_units_of_the_tag_then_migrates_and_restarts(host: Host) -> None:
    result = host.run("deploy", "1.2.0")

    assert result.returncode == 0, result.stderr
    assert actions(host.log) == [
        f"podman pull {REGISTRY}:1.2.0",
        "systemctl --user daemon-reload",
        "systemctl --user enable --quiet mpmv-caddy.socket",
        f"podman tag {REGISTRY}:1.2.0 localhost/mpmv-app:current",
        "systemctl --user start mpmv-migrate.service",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
        *PROXY_ON_SOCKET,
    ]
    image = host.root / "images" / "1.2.0"
    for unit in (image / "quadlet").iterdir():
        assert (host.units / unit.name).read_text() == unit.read_text(), unit.name
    socket = image / "systemd" / "mpmv-caddy.socket"
    assert (host.systemd / socket.name).read_text() == socket.read_text()
    assert not (host.units / socket.name).exists(), "a plain systemd unit, not a Quadlet one"
    assert (host.config / "Caddyfile").read_text() == (image / "caddy" / "Caddyfile").read_text()
    assert host.script.read_bytes() == (image / "server" / "mpmv-deploy").read_bytes()
    assert os.access(host.script, os.X_OK)
    assert host.releases == ["1.2.0"]


def test_deploy_never_touches_the_env_files_or_the_ca(host: Host) -> None:
    kept = ["mpmv.env", "database.env", "caddy.env", "pg-root-ca.crt"]
    before = {name: digest(host.config / name) for name in kept}

    assert host.run("deploy", "1.0.0").returncode == 0
    assert host.run("deploy", "1.1.0").returncode == 0
    assert host.run("rollback").returncode == 0

    assert {name: digest(host.config / name) for name in kept} == before


def test_the_check_goes_through_caddy_by_the_site_name_of_caddy_env(host: Host) -> None:
    (host.root / "media").write_text(MEDIA_FILE)

    result = host.run("deploy", "1.0.0")

    assert result.returncode == 0, result.stderr
    curls = [line for line in host.log if line.startswith("curl ")]
    assert curls
    assert all(f"--resolve {SITE}:443:127.0.0.1" in line for line in curls)
    urls = [line.rsplit(" ", 1)[-1] for line in curls]
    assert urls == [
        f"https://{SITE}/healthz",
        f"https://{SITE}/",
        f"https://{SITE}/media/original_images/demo.webp",
    ]
    assert "podman healthcheck run mpmv-app" in host.log


def test_the_check_trusts_an_extra_ca_only_when_asked(host: Host) -> None:
    ca = host.root / "proxy-ca.crt"
    assert host.run("deploy", "1.0.0", MPMV_CHECK_CACERT=str(ca)).returncode == 0
    assert all(f"--cacert {ca}" in line for line in host.log if line.startswith("curl "))
    assert host.run("check").returncode == 0
    assert not any("--cacert" in line for line in host.log)


def test_stale_units_are_removed(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "images" / "1.1.0" / "quadlet" / "mpmv-worker.container").unlink()

    assert host.run("deploy", "1.1.0").returncode == 0

    assert not (host.units / "mpmv-worker.container").exists()
    assert host.installed_release() == "1.1.0"


def test_caddy_restarts_only_when_its_config_changes(host: Host) -> None:
    host.release("2.0.0", caddy="changed")
    assert host.run("deploy", "1.0.0").returncode == 0

    assert host.run("deploy", "1.0.1").returncode == 0
    assert "systemctl --user restart mpmv-app.service mpmv-worker.service" in host.log
    assert not any(line.endswith("mpmv-caddy.service") for line in actions(host.log))

    assert host.run("deploy", "2.0.0").returncode == 0
    assert actions(host.log)[-3:] == PROXY_ON_SOCKET


def test_deploy_reuses_a_release_already_in_local_storage(host: Host) -> None:
    """Release tags are immutable: a local copy is the release, no pull needed (ARC.13)."""
    (host.root / "local").write_text(f"{REGISTRY}:1.2.0\n")
    result = host.run("deploy", "1.2.0")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("podman pull") for line in host.log)
    assert f"podman tag {REGISTRY}:1.2.0 localhost/mpmv-app:current" in host.log


def test_a_release_without_units_is_refused_before_any_change(host: Host) -> None:
    shutil.rmtree(host.root / "images" / "1.2.0" / "quadlet")

    result = host.run("deploy", "1.2.0")

    assert result.returncode != 0
    assert "carries no deploy units" in result.stderr
    assert actions(host.log) == [f"podman pull {REGISTRY}:1.2.0"]
    assert not host.units.exists()


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


# --- Rollback: the previous image and the previous units.


def test_rollback_restores_the_previous_image_and_units_without_migrating(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    assert host.run("deploy", "1.1.0").returncode == 0

    result = host.run("rollback")

    assert result.returncode == 0, result.stderr
    assert f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current" in host.log
    assert not any("mpmv-migrate" in line for line in host.log)
    assert host.installed_release() == "1.0.0"
    assert host.releases == ["1.0.0"]


def test_rollback_needs_a_previous_release(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0

    result = host.run("rollback")

    assert result.returncode != 0
    assert not any(line.startswith("podman tag") for line in host.log)
    assert host.releases == ["1.0.0"]


@pytest.mark.parametrize("failure", ["bad", "http-bad", "restart-fails"])
def test_a_bad_release_is_rolled_back_with_its_units(host: Host, failure: str) -> None:
    # Unhealthy, healthy but serving errors through Caddy, or not starting at all.
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / failure).write_text("1.1.0\n")

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert "release 1.1.0 failed" in result.stderr
    # The log proves the bad units went in and the live ones came back (ARC.23 parity smoke).
    messages = result.stderr
    assert (
        messages.index("installed the units of 1.1.0")
        < messages.index("rolling back to 1.0.0")
        < messages.index("installed the units of 1.0.0")
    )
    assert actions(host.log)[-4:] == [
        "systemctl --user daemon-reload",
        "systemctl --user enable --quiet mpmv-caddy.socket",
        f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
    ]
    assert host.live_tag == "1.0.0"
    assert host.installed_release() == "1.0.0"
    assert host.releases == ["1.0.0"]


def test_a_crash_looping_release_is_caught_before_the_budget_runs_out(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "bad").write_text("1.1.0\n")
    (host.root / "crashes").write_text("1.1.0\n")

    started = time.monotonic()
    result = host.run("deploy", "1.1.0", MPMV_CHECK_BUDGET="50", MPMV_CHECK_INTERVAL="1")

    assert result.returncode != 0
    assert "restarted on its own" in result.stderr
    assert time.monotonic() - started < 10
    assert host.installed_release() == "1.0.0"


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
    assert actions(host.log)[-2:] == [
        f"podman tag {REGISTRY}:1.1.0 localhost/mpmv-app:current",
        "systemctl --user restart mpmv-app.service mpmv-worker.service",
    ]
    assert host.live_tag == "1.1.0"
    assert host.installed_release() == "1.1.0"
    assert host.releases == ["1.0.0", "1.1.0"]


def test_failed_migration_restores_the_live_units_and_tag_and_keeps_services(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.root / "migrate-fails").touch()

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert actions(host.log)[-1] == f"podman tag {REGISTRY}:1.0.0 localhost/mpmv-app:current"
    assert not any("restart" in line for line in host.log)
    assert host.installed_release() == "1.0.0"
    assert host.releases == ["1.0.0"]


def test_units_installed_by_hand_are_kept_as_the_live_release_set(host: Host) -> None:
    """The first deploy with baked-in units saves the hand-copied ones; rollback restores them."""
    host.units.mkdir(parents=True)
    for unit in (DEPLOY / "quadlet").iterdir():
        shutil.copy(unit, host.units / unit.name)
    shutil.copytree(DEPLOY / "systemd", host.systemd)
    app = host.units / "mpmv-app.container"
    app.write_text(f"{app.read_text()}# release by-hand\n")
    (host.root / "state").mkdir()
    (host.root / "state" / "releases").write_text("0.9.0\n")
    (host.root / "bad").write_text("1.1.0\n")

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert host.installed_release() == "by-hand"
    assert host.releases == ["0.9.0"]


# --- Proxy sockets: Caddy behind systemd socket activation (OPS.5 follow-up).


def test_the_socket_unit_dir_can_be_moved_like_the_quadlet_dir(host: Host) -> None:
    elsewhere = host.root / "units-elsewhere"
    result = host.run("deploy", "1.0.0", MPMV_SYSTEMD_DIR=str(elsewhere))
    assert result.returncode == 0, result.stderr
    assert (elsewhere / "mpmv-caddy.socket").exists()
    assert not host.systemd.exists()


def test_switching_to_the_socket_stops_the_publishing_proxy_before_binding(host: Host) -> None:
    """The first deploy with the socket: the old Caddy holds 80 and 443 until it stops."""
    host.release("0.9.0", published_ports=True)
    assert host.run("deploy", "0.9.0").returncode == 0
    assert actions(host.log)[-3:] == PROXY_PUBLISHING
    assert not host.systemd.exists()

    result = host.run("deploy", "1.0.0")

    assert result.returncode == 0, result.stderr
    assert "systemctl --user enable --quiet mpmv-caddy.socket" in host.log
    assert actions(host.log)[-3:] == PROXY_ON_SOCKET
    assert (host.root / "socket-active").exists()
    assert (host.root / "caddy-active").exists()


def test_rollback_to_a_release_without_the_socket_releases_the_ports(host: Host) -> None:
    host.release("0.9.0", published_ports=True)
    assert host.run("deploy", "0.9.0").returncode == 0
    assert host.run("deploy", "1.0.0").returncode == 0

    result = host.run("rollback")

    assert result.returncode == 0, result.stderr
    assert "systemctl --user disable --quiet mpmv-caddy.socket" in host.log
    assert not (host.systemd / "mpmv-caddy.socket").exists()
    assert actions(host.log)[-3:] == PROXY_PUBLISHING
    assert not (host.root / "socket-active").exists()
    assert "PublishPort=80:80" in (host.units / "mpmv-caddy.container").read_text()
    assert host.releases == ["0.9.0"]


def test_a_socket_that_cannot_bind_rolls_back_to_the_publishing_proxy(host: Host) -> None:
    host.release("0.9.0", published_ports=True)
    assert host.run("deploy", "0.9.0").returncode == 0
    (host.root / "socket-fails").touch()

    result = host.run("deploy", "1.0.0")

    assert result.returncode != 0
    assert "rolling back to 0.9.0" in result.stderr
    assert actions(host.log)[-3:] == PROXY_PUBLISHING
    assert not (host.systemd / "mpmv-caddy.socket").exists()
    assert (host.root / "caddy-active").exists()
    assert host.releases == ["0.9.0"]


def test_a_failed_migration_takes_the_new_socket_back_out(host: Host) -> None:
    host.release("0.9.0", published_ports=True)
    assert host.run("deploy", "0.9.0").returncode == 0
    (host.root / "migrate-fails").touch()

    assert host.run("deploy", "1.0.0").returncode != 0

    assert not (host.systemd / "mpmv-caddy.socket").exists()
    assert "systemctl --user disable --quiet mpmv-caddy.socket" in host.log
    proxy_moves = [
        line
        for line in host.log
        if line.split(" ")[2:3] in (["start"], ["stop"], ["restart"]) and "mpmv-caddy" in line
    ]
    assert proxy_moves == [], "the running proxy is never touched"


def test_a_release_whose_proxy_needs_a_socket_it_lacks_is_refused(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    shutil.rmtree(host.release("2.0.0") / "systemd")

    result = host.run("deploy", "2.0.0")

    assert result.returncode != 0
    assert "need mpmv-caddy.socket" in result.stderr
    assert actions(host.log) == [f"podman pull {REGISTRY}:2.0.0"]
    assert host.installed_release() == "1.0.0"


def test_the_doctor_checks_the_installed_socket_unit(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    (host.systemd / "mpmv-caddy.socket").unlink()

    result = host.run("doctor")

    assert result.returncode != 0
    assert "needs mpmv-caddy.socket" in result.stderr


@pytest.mark.parametrize(("start", "ok"), [("80", True), ("0", True), ("1024", False)])
def test_the_doctor_checks_that_the_user_may_bind_80_and_443(
    host: Host, start: str, ok: bool
) -> None:
    proc = host.root / "proc-sys"
    (proc / "net" / "ipv4").mkdir(parents=True)
    (proc / "net" / "ipv4" / "ip_unprivileged_port_start").write_text(f"{start}\n")

    result = host.run("deploy", "1.0.0", MPMV_PROC_SYS=str(proc))

    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert "ip_unprivileged_port_start is 1024" in result.stderr
        assert actions(host.log) == []


# --- Doctor: refuse a host that cannot run the release, before changing anything.


def test_doctor_passes_on_a_ready_host(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    result = host.run("doctor")
    assert result.returncode == 0, result.stderr
    assert "all checks passed" in result.stderr


def test_a_missing_variable_stops_the_deploy_naming_it_but_no_value(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    mpmv_env = host.config / "mpmv.env"
    lines = mpmv_env.read_text().splitlines()
    values = [line.partition("=")[2] for line in lines]
    mpmv_env.write_text(
        "\n".join(line for line in lines if not line.startswith("MPMV_BASE_URL=")) + "\n"
    )

    result = host.run("deploy", "1.1.0")

    assert result.returncode != 0
    assert "MPMV_BASE_URL" in result.stderr
    assert not any(value and value in result.stderr for value in values if len(value) > 6)
    assert actions(host.log) == [f"podman pull {REGISTRY}:1.1.0"]
    assert host.installed_release() == "1.0.0"


def test_units_the_host_podman_cannot_convert_stop_the_deploy(host: Host) -> None:
    assert host.run("deploy", "1.0.0").returncode == 0
    host.release("2.0.0", app_extra="UnknownToThisPodman=1\n")

    result = host.run("deploy", "2.0.0")

    assert result.returncode != 0
    assert "do not convert" in result.stderr
    assert actions(host.log) == [f"podman pull {REGISTRY}:2.0.0"]
    assert host.installed_release() == "1.0.0"


@pytest.mark.parametrize(
    ("breakage", "message"),
    [
        ("ca", "pg-root-ca.crt"),
        ("volume", "media volume owned by"),
        ("disk", "free disk"),
        ("podman", "has no 'healthcheck run'"),
        ("env-file", "caddy.env missing"),
    ],
)
def test_doctor_stops_the_deploy_on_a_broken_host(host: Host, breakage: str, message: str) -> None:
    extra = {}
    if breakage == "ca":
        (host.config / "pg-root-ca.crt").unlink()
    elif breakage == "volume":
        (host.root / "volume-owner").write_text("0:0\n")
    elif breakage == "disk":
        extra["MPMV_MIN_FREE_MB"] = str(10**12)
    elif breakage == "podman":
        (host.root / "podman-lacks").write_text("healthcheck run\n")
    else:
        (host.config / "caddy.env").unlink()

    result = host.run("deploy", "1.0.0", **extra)

    assert result.returncode != 0
    assert message in result.stderr
    assert "nothing changed" in result.stderr
    assert actions(host.log) == []
    assert host.run("doctor", **extra).returncode != 0


def test_without_ipv6_a_socket_release_is_refused_before_any_change(host: Host) -> None:
    net = host.root / "proc-net-empty"
    net.mkdir()
    (net / "if_inet6").write_text("")

    result = host.run("deploy", "1.0.0", MPMV_PROC_NET=str(net))

    assert result.returncode != 0
    assert "no IPv6 address" in result.stderr
    assert actions(host.log) == [f"podman pull {REGISTRY}:1.0.0"]
    assert not (host.systemd / "mpmv-caddy.socket").exists()


def test_deploy_continues_with_the_release_own_script(host: Host) -> None:
    """An older installed script hands the deploy to the release's script (socket switch)."""
    own = host.root / "images" / "1.0.0" / "server" / "mpmv-deploy"
    marker = host.root / "reexec-marker"
    own.write_text(f'#!/bin/sh\necho "$*" > "{marker}"\n')

    result = host.run("deploy", "1.0.0")

    assert result.returncode == 0, result.stderr
    assert marker.read_text().strip() == "deploy 1.0.0"
    assert "continuing with the deploy script of 1.0.0" in result.stderr


def test_the_script_has_no_reexec_environment_bypass() -> None:
    """The cmp against the release's script ends the hand-over: no variable is needed."""
    assert "MPMV_REEXEC" not in SCRIPT.read_text()


def marked(text: str, line: str) -> str:
    """The deploy script with one extra log line right after `set -eu`."""
    assert text.count("\nset -eu\n") == 1
    return text.replace("\nset -eu\n", f"\nset -eu\n{line}\n")


def test_the_previous_scripts_deploy_re_execs_the_candidate_script(host: Host) -> None:
    """Old to new: the previous release's script deploys the candidate, then runs the candidate's
    own script (once), which finishes the deploy and installs itself as the host's script."""
    text = SCRIPT.read_text()
    old = host.root / "old-mpmv-deploy"
    old.write_text(marked(text, 'echo "ran the previous script" >&2'))
    candidate = host.root / "images" / "1.2.0" / "server" / "mpmv-deploy"
    candidate.write_text(marked(text, 'echo "ran the candidate script $0" >&2'))
    assert old.read_text() != candidate.read_text()

    result = host.run("deploy", "1.2.0", script=old)

    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    assert lines.count("ran the previous script") == 1
    assert lines.count("mpmv-deploy: continuing with the deploy script of 1.2.0") == 1
    own = str(host.root / "state" / "units" / "1.2.0" / "server" / "mpmv-deploy")
    assert lines.count(f"ran the candidate script {own}") == 1
    # The hand-over came before any change, and the candidate's script did the rest.
    assert lines.index("ran the previous script") < lines.index(f"ran the candidate script {own}")
    assert "mpmv-deploy: release 1.2.0 is live" in lines
    assert host.releases == ["1.2.0"]
    assert host.script.read_bytes() == candidate.read_bytes()
    assert host.log.count(f"podman pull {REGISTRY}:1.2.0") == 1


def test_a_script_equal_to_the_release_script_does_not_re_exec(host: Host) -> None:
    result = host.run("deploy", "1.2.0")

    assert result.returncode == 0, result.stderr
    assert "continuing with the deploy script" not in result.stderr
