"""Automated dependency updates and SBOM (OPS.4, spec 12 §12.5).

Dependabot opens weekly update PRs for the uv lockfile, the GitHub Actions and the container base
images. Minor and patch updates arrive grouped, major updates one per PR, and every update PR runs
the full CI. CI and releases publish a CycloneDX SBOM of the Python environment and of the image.
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"
MAKEFILE = ROOT / "Makefile"
GUIDE = ROOT / "docs" / "dev" / "dependency-updates.md"

ECOSYSTEMS = {"uv", "github-actions", "docker"}
SBOM_ACTION = "anchore/sbom-action@"
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
# uv is still 0.x: its minor releases carry breaking changes, so its image is never grouped.
UV_IMAGE = "astral-sh/uv"


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} is not a mapping"
    return data


def triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    """The `on:` block; YAML 1.1 reads the bare key `on` as the boolean True."""
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def steps(workflow: dict[Any, Any], job: str) -> list[dict[str, Any]]:
    result = workflow["jobs"][job]["steps"]
    assert isinstance(result, list)
    return result


def make_recipe(target: str) -> str:
    """The recipe lines of a Makefile target, joined."""
    match = re.search(
        rf"^{re.escape(target)}:.*\n((?:\t.*\n?)+)", MAKEFILE.read_text(), re.MULTILINE
    )
    assert match, f"Makefile has no target {target}"
    return match.group(1)


@pytest.fixture(scope="module")
def updates() -> dict[str, dict[str, Any]]:
    config = load(DEPENDABOT)
    assert config["version"] == 2
    return {entry["package-ecosystem"]: entry for entry in config["updates"]}


def test_dependabot_covers_python_actions_and_base_images(
    updates: dict[str, dict[str, Any]],
) -> None:
    assert set(updates) == ECOSYSTEMS
    for entry in updates.values():
        assert entry["directory"] == "/"
    # Dependabot's docker ecosystem reads the Containerfile in that directory.
    assert (ROOT / "Containerfile").is_file()
    assert (ROOT / "uv.lock").is_file()


def test_updates_run_weekly(updates: dict[str, dict[str, Any]]) -> None:
    for name, entry in updates.items():
        assert entry["schedule"]["interval"] == "weekly", name


def test_each_ecosystem_sets_its_open_pull_request_limit(
    updates: dict[str, dict[str, Any]],
) -> None:
    # Set explicitly, so a grouped PR plus a few majors are never held back by the default.
    for name, entry in updates.items():
        assert "open-pull-requests-limit" in entry, name
        assert entry["open-pull-requests-limit"] >= 5, name


def test_minor_and_patch_updates_are_grouped_and_majors_are_not(
    updates: dict[str, dict[str, Any]],
) -> None:
    for name, entry in updates.items():
        groups = entry["groups"].values()
        grouped = [group for group in groups if group.get("applies-to") == "version-updates"]
        assert grouped, f"{name}: no group for version updates"
        for group in grouped:
            assert sorted(group["update-types"]) == ["minor", "patch"], name


def test_major_updates_are_never_grouped(updates: dict[str, dict[str, Any]]) -> None:
    # A group with no `applies-to` applies to version updates, and one with no `update-types`
    # takes every update type, majors included. Security updates are not grouped either.
    for name, entry in updates.items():
        for group_name, group in entry["groups"].items():
            label = f"{name}: {group_name}"
            assert group.get("applies-to") == "version-updates", label
            assert "update-types" in group, label
            assert "major" not in group["update-types"], label


def test_uv_image_gets_its_own_pr(updates: dict[str, dict[str, Any]]) -> None:
    assert f"/{UV_IMAGE}:" in (ROOT / "Containerfile").read_text()
    for group_name, group in updates["docker"]["groups"].items():
        assert UV_IMAGE in group.get("exclude-patterns", []), group_name


def test_framework_upgrades_get_their_own_pr(updates: dict[str, dict[str, Any]]) -> None:
    excluded = {
        pattern
        for group in updates["uv"]["groups"].values()
        for pattern in group.get("exclude-patterns", [])
    }
    assert {"django", "wagtail"} <= excluded


def test_python_base_image_stays_on_the_runtime_minor(
    updates: dict[str, dict[str, Any]],
) -> None:
    # A new Python minor touches requires-python, .python-version and CI: a planned change.
    rules = [
        rule for rule in updates["docker"].get("ignore", []) if "python" in rule["dependency-name"]
    ]
    blocked = {kind for rule in rules for kind in rule["update-types"]}
    assert {"version-update:semver-major", "version-update:semver-minor"} <= blocked


def test_update_prs_are_labelled_and_follow_the_task_id_convention(
    updates: dict[str, dict[str, Any]],
) -> None:
    for name, entry in updates.items():
        assert "dependencies" in entry["labels"], name
        assert entry["commit-message"]["prefix"] == "OPS.4", name


def test_updates_wait_a_cooldown_after_each_release(updates: dict[str, dict[str, Any]]) -> None:
    # Supply-chain guard: a freshly published (possibly hijacked) release is not picked up at once.
    for name, entry in updates.items():
        assert entry["cooldown"]["default-days"] >= 3, name


# Dependabot refuses the whole file when a semver-specific cooldown is set for an ecosystem that
# does not version by semver (it reported this for github-actions and docker on the first run).
SEMVER_COOLDOWN_ECOSYSTEMS = {"uv"}


def test_semver_cooldowns_are_set_only_where_dependabot_supports_them(
    updates: dict[str, dict[str, Any]],
) -> None:
    for name, entry in updates.items():
        semver_keys = {key for key in entry["cooldown"] if key.startswith("semver-")}
        if name not in SEMVER_COOLDOWN_ECOSYSTEMS:
            assert not semver_keys, name


def test_dependabot_config_needs_no_secrets() -> None:
    text = DEPENDABOT.read_text()
    assert "registries" not in load(DEPENDABOT)
    assert "${{" not in text
    assert "secrets." not in text


@pytest.mark.parametrize(
    ("schema", "paths"),
    [
        ("vendor.dependabot", [DEPENDABOT]),
        ("vendor.github-workflows", sorted(WORKFLOWS.glob("*.yml"))),
    ],
    ids=["dependabot", "workflows"],
)
def test_configuration_matches_the_published_schema(schema: str, paths: list[Path]) -> None:
    tool = shutil.which("check-jsonschema")
    assert tool, "check-jsonschema is a dev dependency"
    result = subprocess.run(  # noqa: S603 (fixed tool, repository paths)
        [tool, "--builtin-schema", schema, *map(str, paths)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_update_prs_run_the_full_ci() -> None:
    ci = load(CI)
    pull_request = triggers(ci)["pull_request"] or {}
    for narrowing in ("branches", "branches-ignore", "paths", "paths-ignore"):
        assert narrowing not in pull_request, f"pull_request.{narrowing} would skip update PRs"
    for name, job in ci["jobs"].items():
        assert "dependabot" not in str(job.get("if", "")), f"job {name} skips update PRs"


def test_workflow_actions_are_pinned_to_a_version() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in load(path)["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses:
                    ref = uses.split("@", 1)[1] if "@" in uses else ""
                    assert re.fullmatch(r"v\d+(\.\d+)*|[0-9a-f]{40}", ref), f"{path.name}: {uses}"


def test_python_sbom_make_target_covers_the_production_environment() -> None:
    recipe = make_recipe("sbom")
    assert "--no-dev" in recipe
    assert "cyclonedx-py environment" in recipe
    assert "--pyproject pyproject.toml" in recipe
    assert "build/sbom/" in recipe


def test_ci_publishes_python_and_image_sboms() -> None:
    ci = load(CI)
    static = steps(ci, "static")
    assert any(step.get("run", "").strip() == "make sbom" for step in static)
    image = steps(ci, "image")
    sbom = [step for step in image if step.get("uses", "").startswith(SBOM_ACTION)]
    assert sbom, "the image job must generate an SBOM of the built image"
    assert sbom[0]["with"]["image"] == "mpmv-app:ci"
    assert sbom[0]["with"]["format"] == "cyclonedx-json"
    uploads = [
        step["with"]["path"]
        for job in ("static", "image")
        for step in steps(ci, job)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert any("build/sbom" in str(path) for path in uploads)


def test_release_attaches_both_sboms_to_the_github_release() -> None:
    release = load(RELEASE)
    job_steps = steps(release, "image")
    assert any(step.get("run", "").strip() == "make sbom" for step in job_steps)
    sbom = [step for step in job_steps if step.get("uses", "").startswith(SBOM_ACTION)]
    assert sbom, "the release must generate an SBOM of the published image"
    assert sbom[0]["with"]["format"] == "cyclonedx-json"
    uploads = [
        step for step in job_steps if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert any("build/sbom" in str(step["with"]["path"]) for step in uploads)
    attach = release["jobs"]["attach"]
    assert attach["needs"] == "image"
    attach_steps = steps(release, "attach")
    assert any(
        step.get("uses", "").startswith("actions/download-artifact@") for step in attach_steps
    )
    scripts = " ".join(step.get("run", "") for step in attach_steps)
    assert "gh release upload" in scripts
    assert "build/sbom/" in scripts


def test_release_grants_write_access_to_the_repository_only_to_the_attach_job() -> None:
    # The image job runs the build and a third-party action: it must not be able to write to
    # the repository. The small attach job creates the release and uploads the SBOMs.
    jobs = load(RELEASE)["jobs"]
    assert jobs["image"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs["attach"]["permissions"] == {"contents": "write"}
    for step in jobs["attach"]["steps"]:
        uses = step.get("uses", "")
        assert not uses or uses.startswith("actions/"), f"attach job runs {uses}"


def test_release_checkout_does_not_persist_the_token() -> None:
    for job_name, job in load(RELEASE)["jobs"].items():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") is False, job_name


def test_third_party_actions_with_write_access_are_pinned_by_commit() -> None:
    # A moved tag cannot swap the code that runs with a write token.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = load(path)
        default = workflow.get("permissions", {})
        for job_name, job in workflow["jobs"].items():
            permissions = job.get("permissions", default)
            if "write" not in permissions.values():
                continue
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if uses and not uses.startswith("actions/"):
                    ref = uses.split("@", 1)[1]
                    assert COMMIT_SHA.fullmatch(ref), f"{path.name} {job_name}: {uses}"


def test_review_guide_is_documented() -> None:
    text = GUIDE.read_text()
    for topic in ("Dependabot", "major", "security", "SBOM", "pip-audit"):
        assert topic in text, topic


@pytest.mark.parametrize(("path", "job"), [(CI, "image"), (RELEASE, "image")])
def test_image_sbom_falls_back_to_the_syft_container_and_never_goes_missing(
    path: Path, job: str
) -> None:
    """GitHub release downloads can fail (HTTP 504): the action may fail, the SBOM may not."""
    job_steps = steps(load(path), job)
    names = [step.get("name") for step in job_steps]
    action = job_steps[names.index("Image SBOM (CycloneDX)")]
    fallback = job_steps[names.index("Image SBOM fallback (syft container)")]
    present = job_steps[names.index("Image SBOM present")]
    assert action["id"] == "sbom"
    assert action["continue-on-error"] is True
    assert fallback["if"] == "steps.sbom.outcome == 'failure'"
    assert re.search(r"anchore/syft:v\d+\.\d+\.\d+ ", fallback["run"])
    assert "cyclonedx-json=/out/mpmv-image.cdx.json" in fallback["run"]
    assert "if" not in present
    assert "continue-on-error" not in present
    assert present["run"] == "test -s build/sbom/mpmv-image.cdx.json"
    assert (
        names.index("Image SBOM (CycloneDX)")
        < names.index("Image SBOM fallback (syft container)")
        < names.index("Image SBOM present")
    )
