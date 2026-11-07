"""Lint rules for the OCI image definition (ARC.5, ADR-0002).

The image must be multi-stage, built with uv, run as a non-root user, use pinned base images and
never bake secrets into its layers.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
IGNORE_FILE = ROOT / ".dockerignore"

SECRET_LIKE = re.compile(r"SECRET|PASSW|TOKEN|CREDENTIAL|PRIVATE|(^|_)KEY($|_)", re.IGNORECASE)
PINNED_IMAGE = re.compile(r"^[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class Instruction:
    keyword: str
    args: str


def parse(text: str) -> list[Instruction]:
    """Split a Containerfile into instructions, joining continuation lines, dropping comments."""
    instructions: list[Instruction] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].rstrip() + " "
            continue
        buffer += line
        keyword, _, args = buffer.strip().partition(" ")
        instructions.append(Instruction(keyword.upper(), args.strip()))
        buffer = ""
    return instructions


def stages(instructions: list[Instruction]) -> list[list[Instruction]]:
    """Group instructions by build stage; each stage starts with its FROM."""
    result: list[list[Instruction]] = []
    for instruction in instructions:
        if instruction.keyword == "FROM":
            result.append([])
        if result:
            result[-1].append(instruction)
    return result


def variable_names(instruction: Instruction) -> list[str]:
    """Names declared by an ENV or ARG instruction (`A=1 B=2`, `A 1` or `A`)."""
    tokens = instruction.args.split()
    if instruction.keyword == "ENV" and tokens and "=" not in tokens[0]:
        return [tokens[0]]
    return [token.split("=", 1)[0] for token in tokens if re.match(r"^[A-Za-z_]\w*(=|$)", token)]


@pytest.fixture(scope="module")
def instructions() -> list[Instruction]:
    return parse(CONTAINERFILE.read_text())


def test_parser_joins_continuations_and_skips_comments() -> None:
    parsed = parse("# comment\nFROM a:1 AS b\nRUN echo 1 \\\n    && echo 2\n")
    assert parsed == [Instruction("FROM", "a:1 AS b"), Instruction("RUN", "echo 1 && echo 2")]


@pytest.mark.parametrize(
    ("args", "expected"),
    [("A=1 B=2", ["A", "B"]), ("A 1", ["A"]), ("PATH=/app/.venv/bin:$PATH", ["PATH"])],
)
def test_variable_names(args: str, expected: list[str]) -> None:
    assert variable_names(Instruction("ENV", args)) == expected


def test_containerfile_is_multi_stage(instructions: list[Instruction]) -> None:
    assert len(stages(instructions)) >= 2


def test_external_base_images_are_pinned_by_tag_and_digest(
    instructions: list[Instruction],
) -> None:
    aliases: set[str] = set()
    for instruction in instructions:
        if instruction.keyword != "FROM":
            continue
        parts = instruction.args.split()
        image = next(part for part in parts if not part.startswith("--"))
        if image not in aliases:
            assert PINNED_IMAGE.match(image), f"base image not pinned: {image}"
            assert ":latest@" not in image
        if len(parts) >= 3 and parts[-2].upper() == "AS":
            aliases.add(parts[-1])


def test_runtime_is_python_3_13(instructions: list[Instruction]) -> None:
    final_from = stages(instructions)[-1][0]
    assert "python:3.13" in final_from.args


def test_dependencies_are_installed_with_uv_from_the_lockfile(
    instructions: list[Instruction],
) -> None:
    runs = [i.args for i in instructions if i.keyword == "RUN"]
    sync = [run for run in runs if "uv sync" in run]
    assert sync, "dependencies must be installed with `uv sync`"
    assert all("--frozen" in run or "--locked" in run for run in sync)
    assert all("--no-dev" in run for run in sync)
    copies = [i.args for i in instructions if i.keyword == "COPY"]
    assert any("uv.lock" in copy for copy in copies)


def test_static_files_are_collected_at_build_time(instructions: list[Instruction]) -> None:
    runs = [i.args for i in instructions if i.keyword == "RUN"]
    assert any("collectstatic" in run and "--noinput" in run for run in runs)


def test_final_stage_runs_as_a_non_root_user(instructions: list[Instruction]) -> None:
    final = stages(instructions)[-1]
    users = [i.args.split(":")[0] for i in final if i.keyword == "USER"]
    assert users, "the final stage must declare USER"
    assert users[-1] not in {"root", "0"}
    last_user = max(n for n, i in enumerate(final) if i.keyword == "USER")
    entrypoint = next(n for n, i in enumerate(final) if i.keyword == "ENTRYPOINT")
    assert last_user < entrypoint, "USER must be set before ENTRYPOINT"


def test_no_secret_like_env_or_arg(instructions: list[Instruction]) -> None:
    for instruction in instructions:
        if instruction.keyword in {"ENV", "ARG"}:
            for name in variable_names(instruction):
                assert not SECRET_LIKE.search(name), f"{instruction.keyword} {name} looks secret"


def test_no_secret_files_are_copied(instructions: list[Instruction]) -> None:
    for instruction in instructions:
        if instruction.keyword in {"COPY", "ADD"}:
            assert ".env" not in instruction.args
            assert "private" not in instruction.args


def test_image_records_its_version(instructions: list[Instruction]) -> None:
    labels = " ".join(i.args for i in instructions if i.keyword == "LABEL")
    assert "org.opencontainers.image.version" in labels
    envs = [name for i in instructions if i.keyword == "ENV" for name in variable_names(i)]
    assert "MPMV_VERSION" in envs


def test_build_context_excludes_secrets_and_local_state() -> None:
    patterns = {
        line.strip()
        for line in IGNORE_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    for required in (".env", ".env.*", ".git", ".venv", "private", "CLAUDE.local.md", ".claude"):
        assert required in patterns, f"{required} must be excluded from the build context"
