"""Multi-arch release image (ARC.13, ADR-0007 amendment 2026-11-08).

The image is built for linux/amd64 and linux/arm64, each on a native GitHub runner, without QEMU
emulation. Pull requests build and check both architectures; a release tag publishes one
multi-arch manifest. Branch protection requires the check "Container image", which must pass
only when both architectures pass.
"""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"
CONTAINERFILE = ROOT / "Containerfile"

NATIVE_RUNNERS = {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}
REQUIRED_CHECK = "Container image"


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} is not a mapping"
    return data


def scripts(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def assert_native_matrix(job: dict[str, Any]) -> None:
    include = job["strategy"]["matrix"]["include"]
    assert {entry["arch"]: entry["runner"] for entry in include} == NATIVE_RUNNERS
    assert job["runs-on"] == "${{ matrix.runner }}"


def test_no_workflow_emulates_another_architecture() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in load(path)["jobs"].items():
            for step in job.get("steps", []):
                code = f"{step.get('uses', '')} {step.get('run', '')}".lower()
                assert "qemu" not in code, f"{path.name} {name}"
                assert "binfmt" not in code, f"{path.name} {name}"


def test_containerfile_has_no_architecture_specific_bits() -> None:
    # Base images are pinned by the digest of their multi-arch index, and every Python
    # dependency ships wheels for both architectures: nothing in the build names one.
    text = CONTAINERFILE.read_text().lower()
    for literal in ("amd64", "x86_64", "arm64", "aarch64", "--platform"):
        assert literal not in text, literal


def test_ci_builds_and_checks_the_image_on_both_native_runners() -> None:
    build = load(CI)["jobs"]["image-build"]
    assert_native_matrix(build)
    # Both legs report, so an arm64 failure is visible even when amd64 fails too.
    assert build["strategy"]["fail-fast"] is False
    run = scripts(build)
    assert "docker build" in run
    assert "docker push" not in run
    assert "push=true" not in run
    # The image really is the runner's architecture.
    assert "{{.Architecture}}" in run
    assert "${{ matrix.arch }}" in run


def test_ci_required_check_passes_only_when_both_architectures_pass() -> None:
    jobs = load(CI)["jobs"]
    named = [key for key, job in jobs.items() if job.get("name") == REQUIRED_CHECK]
    assert named == ["image"]
    gate = jobs["image"]
    assert gate["needs"] == "image-build"
    # Without always() a failed build would skip the gate, and a skipped required check passes.
    assert gate["if"] == "always()"
    assert gate["env"]["BUILD_RESULT"] == "${{ needs.image-build.result }}"
    assert 'test "$BUILD_RESULT" = success' in scripts(gate)


def test_release_builds_each_architecture_natively_and_pushes_by_digest() -> None:
    image = load(RELEASE)["jobs"]["image"]
    assert_native_matrix(image)
    assert image["strategy"]["fail-fast"] is True
    run = scripts(image)
    assert '--platform "linux/$ARCH"' in run
    assert "push-by-digest=true" in run
    # No per-architecture tag is ever published: only the merged manifest gets tags.
    assert "docker push" not in run
    assert '--tag "$IMAGE' not in run
    # The Python SBOM does not depend on the architecture: one leg makes it.
    make_sbom = [step for step in image["steps"] if step.get("run", "").strip() == "make sbom"]
    assert len(make_sbom) == 1
    assert make_sbom[0]["if"] == "matrix.arch == 'amd64'"


def test_release_publishes_one_manifest_with_both_architectures() -> None:
    jobs = load(RELEASE)["jobs"]
    publish = jobs["publish"]
    assert publish["needs"] == "image"
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    run = scripts(publish)
    assert "already exists; release tags are immutable" in run
    create = [
        step["run"] for step in publish["steps"] if "imagetools create" in step.get("run", "")
    ]
    assert create, "publish must merge the per-architecture images into one manifest"
    assert '--tag "$IMAGE:$VERSION"' in create[0]
    assert '--tag "$IMAGE:sha-${GITHUB_SHA::12}"' in create[0]
    for arch in NATIVE_RUNNERS:
        assert f'"$IMAGE@$(cat build/digests/{arch})"' in create[0]
    assert "linux/amd64" in run
    assert "linux/arm64" in run
    for step in publish["steps"]:
        uses = step.get("uses", "")
        assert not uses or uses.startswith("actions/"), f"publish job runs {uses}"


def test_release_attaches_one_image_sbom_per_architecture() -> None:
    attach = load(RELEASE)["jobs"]["attach"]
    run = scripts(attach)
    for arch in NATIVE_RUNNERS:
        assert f"build/sbom/mpmv-image-{arch}.cdx.json" in run
    assert "build/sbom/mpmv-python.cdx.json" in run
