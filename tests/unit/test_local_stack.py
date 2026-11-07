"""Safety rules for the local Apple `container` stack (ARC.5, ADR-0002).

Developer machines run unrelated containers and volumes; the stack script may only create, stop
or delete resources whose names start with `mpmv-`, and never the database (see `make db-reset`).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "deploy" / "local" / "stack.sh"
MAKEFILE = ROOT / "Makefile"

DESTRUCTIVE = re.compile(
    r"\bcontainer\s+(?:(?:volume|network|image)\s+)?(stop|delete|rm|kill|prune)\b(.*)"
)


def assignments(script: str) -> dict[str, str]:
    """Top-level `NAME=value` or `NAME=${OVERRIDE:-value}` assignments, resolved to the default."""
    result: dict[str, str] = {}
    for match in re.finditer(r"^([A-Z_]+)=(\S+)$", script, re.MULTILINE):
        value = match.group(2).strip("\"'")
        default = re.fullmatch(r"\$\{\w+:-(.+)\}", value)
        result[match.group(1)] = default.group(1) if default else value
    return result


def test_assignments_resolve_defaults() -> None:
    assert assignments('APP=mpmv-app\nIMAGE="${MPMV_IMAGE:-mpmv-app:dev}"\n') == {
        "APP": "mpmv-app",
        "IMAGE": "mpmv-app:dev",
    }


def test_resource_names_are_prefixed() -> None:
    names = assignments(STACK.read_text())
    for variable in ("NETWORK", "APP", "WORKER", "MEDIA_VOLUME", "IMAGE"):
        assert names[variable].startswith("mpmv-"), variable


def test_destructive_commands_only_target_stack_resources() -> None:
    script = STACK.read_text()
    names = assignments(script)
    allowed = {f'"${var}"' for var in ("APP", "WORKER", "NETWORK", "MEDIA_VOLUME")}
    found = DESTRUCTIVE.findall(script)
    assert found, "the script must be able to tear the stack down"
    for verb, rest in found:
        assert verb in {"stop", "delete"}, f"`container {verb}` is not allowed"
        targets = [token for token in rest.split() if not token.startswith(("-", ">", "2>"))]
        targets = [token for token in targets if token not in {"||", "true", "&&", ";"}]
        assert targets, f"`container {verb}` without explicit targets"
        for target in targets:
            assert target in allowed, f"`container {verb} {target}` targets a foreign resource"
    assert names["DB"] == "mpmv-db"
    assert '"$DB"' not in "".join(rest for _, rest in found), "the stack never deletes the DB"


def test_makefile_exposes_the_stack_targets() -> None:
    makefile = MAKEFILE.read_text()
    for target in ("image", "dev", "dev-down", "dev-reset", "dev-logs"):
        assert re.search(rf"^{target}:", makefile, re.MULTILINE), target
    assert re.search(r"^dev:.*\bdb\b", makefile, re.MULTILINE), "`make dev` starts the database"
