"""Release gate with production parity (ARC.23).

CI runs every built image the way the server runs it (deploy/ci/parity-smoke.sh) on both
architectures, and a release is pushed only after that smoke passes. The image carries its own
units, so the server installs the units of the tag it runs. These rules keep that wiring intact.
"""

import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.core.media import RASTER_CSP

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "deploy" / "ci" / "parity-smoke.sh"
SMOKE_RUN = "deploy/ci/parity-smoke.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
REQUIRED_CHECKS = {
    "Static checks",
    "Secret scan",
    "Unit + integration",
    "Container image",
    "E2E (Playwright)",
}
CREDENTIAL_LITERAL = re.compile(
    r"(PASSWORD|SECRET_KEY|ENCRYPTION_KEYS)=['\"]?[A-Za-z0-9+/_-]{8,}|://[^/$\s:%]+:[^@$\s{%]+@"
)

DEPLOY_CALL = re.compile(r"(\S*)mpmv-deploy (?:deploy|rollback|doctor|check|status)\b")


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def step_names(job: dict[str, Any]) -> list[str]:
    return [step.get("name", step.get("uses", "")) for step in job["steps"]]


def smoke_index(job: dict[str, Any]) -> int:
    runs = [i for i, step in enumerate(job["steps"]) if SMOKE_RUN in step.get("run", "")]
    assert len(runs) == 1, "the job must run the parity smoke exactly once"
    return runs[0]


def ignored(path: str) -> bool:
    patterns = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    parts = path.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return any(fnmatch.fnmatch(prefix, pattern) for prefix in prefixes for pattern in patterns)


# --- The image carries the release's units.


def test_the_image_carries_the_units_caddyfile_and_deploy_script() -> None:
    for path in (
        "deploy/quadlet/mpmv-app.container",
        "deploy/quadlet/mpmv-media.volume",
        "deploy/caddy/Caddyfile",
        "deploy/systemd/mpmv-caddy.socket",
        "deploy/server/mpmv-deploy",
        "deploy/server/mpmv.env.example",
        "deploy/container/entrypoint.sh",
    ):
        assert not ignored(path), f"{path} is left out of the image"


def test_the_image_carries_no_env_file_or_ci_tooling() -> None:
    for path in (".env", "deploy/server/mpmv.env", "private/x", "deploy/ci/parity-smoke.sh"):
        assert ignored(path), f"{path} would enter the image"
    shipped = [p for p in (ROOT / "deploy" / "server").iterdir() if p.name.endswith(".env")]
    assert shipped == []


# --- CI and release run the smoke on both architectures, without new required checks.


def test_ci_runs_the_parity_smoke_on_each_architecture_after_the_build() -> None:
    job = load(CI)["jobs"]["image-build"]
    assert job["name"] == "Container image (${{ matrix.arch }})"
    assert {entry["arch"] for entry in job["strategy"]["matrix"]["include"]} == {"amd64", "arm64"}
    names = step_names(job)
    assert smoke_index(job) > names.index("Build the Containerfile")
    assert "mpmv-app:ci" in job["steps"][smoke_index(job)]["run"]


def test_required_check_names_stay_the_same() -> None:
    names = {job.get("name", key) for key, job in load(CI)["jobs"].items()}
    assert names >= REQUIRED_CHECKS
    assert names - REQUIRED_CHECKS == {"Container image (${{ matrix.arch }})"}


def test_a_release_is_pushed_only_after_the_smoke_passes() -> None:
    job = load(RELEASE)["jobs"]["image"]
    names = step_names(job)
    smoke = smoke_index(job)
    assert names.index("Build") < smoke < names.index("Push by digest")
    assert "mpmv-app:release" in job["steps"][smoke]["run"]
    assert job["strategy"]["fail-fast"] is True
    # The smoke runs third-party images: no registry write token on disk yet.
    assert smoke < names.index("Log in to the registry")
    assert 0 < job["timeout-minutes"] <= 60


@pytest.mark.parametrize(
    "ci_env",
    [
        {},
        {"GITHUB_ACTIONS": "true"},
        {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"},
        {"MPMV_PARITY_THROWAWAY_HOST": "yes"},
    ],
)
def test_the_smoke_refuses_to_run_outside_a_hosted_ci_runner(
    tmp_path: Path, ci_env: dict[str, str]
) -> None:
    """It overwrites ~/.config/mpmv and the units: never on a server or a workstation."""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), **ci_env}
    result = subprocess.run(  # noqa: S603 (fixed script path)
        ["bash", str(SMOKE), "mpmv-app:ci"],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "CI-only" in result.stderr
    assert list(tmp_path.iterdir()) == []


# --- The smoke runs the real thing.


def test_the_smoke_deploys_with_the_release_mpmv_deploy_and_real_units() -> None:
    text = SMOKE.read_text()
    assert "/app/deploy/server/mpmv-deploy" in text
    assert '"$deployer" deploy "$good"' in text
    assert "systemctl --user" in text
    # Units and Caddyfile come from the image, through mpmv-deploy: never copied by the smoke.
    assert "containers/systemd" not in text.replace('units="$HOME/.config/containers/systemd"', "")
    assert not re.search(r"cp [^\n]*Caddyfile", text)


def test_the_smoke_uses_tls_verify_full_and_generated_secrets_only() -> None:
    text = SMOKE.read_text()
    assert "sslmode=verify-full&sslrootcert=/etc/mpmv/pg-root-ca.crt" in text
    assert "ssl=on" in text
    # The server takes TLS only, and the app's own session is checked to be TLS.
    assert "hostssl all all all scram-sha-256" in text
    assert "hba_file=" in text
    assert "pg_stat_ssl WHERE pid = pg_backend_pid()" in text
    assert "sslmode=disable" in text
    assert re.search(r"postgres_image=\S+:17@sha256:[0-9a-f]{64}\n", text)
    assert "secrets.token" in text
    assert not CREDENTIAL_LITERAL.search(text), CREDENTIAL_LITERAL.search(text)


def test_the_smoke_checks_pages_media_health_and_rollback() -> None:
    text = SMOKE.read_text()
    for needle in (
        "MPMV_ALLOW_SEED=1 mpmv-app python manage.py seed",
        "expect_status /healthz 200",
        "expect_status / 200",
        'expect_status "$category" 200',
        "content-type: image/webp",
        "x-content-type-options: nosniff",
        f"content-security-policy: {RASTER_CSP}",
        "media without HSTS",
        "names the server software",
        "lists a media directory",
        "cache-control: public",
        "expect_status /media/documents/parity.pdf 404",
        "expect_status /media/images/parity.html 404",
        "cache-control: no-store",
        "oversized body",
        "413",
        "can write outside /app/media",
        '"$deployer" deploy "$bad"',
        '[ "$took" -lt 60 ]',
        # Proof of a real rollback, not an early stop that also exits non-zero.
        "installed the units of $bad",
        "rolling back to $good",
        "installed the units of $good",
        "doctor:|nothing changed",
        "trap on_exit EXIT",
    ):
        assert needle in text, needle


def test_the_production_caddyfile_has_no_test_branches() -> None:
    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text()
    for needle in ("localhost", "tls internal", "local_certs", "parity", "{$CI"):
        assert needle not in caddyfile, needle


# --- Release rules.


def test_release_rules_are_documented_and_linked() -> None:
    rules = ROOT / "docs" / "dev" / "release.md"
    text = rules.read_text()
    for needle in ("full SHA", "doctor", "rollback", "STAGE", "LIVE", "same day"):
        assert needle in text, needle
    assert "docs/dev/release.md" in (ROOT / "deploy" / "README.md").read_text()
    assert "docs/dev/release.md" in (ROOT / "CONTRIBUTING.md").read_text()


def test_the_docs_call_mpmv_deploy_by_its_absolute_path() -> None:
    """Non-interactive ssh has no ~/.local/bin on PATH: every documented call names the path."""
    for doc in (ROOT / "deploy" / "README.md", ROOT / "docs" / "dev" / "release.md"):
        calls = DEPLOY_CALL.findall(doc.read_text())
        assert calls, doc.name
        assert all(prefix.endswith("~/.local/bin/") for prefix in calls), (doc.name, calls)
