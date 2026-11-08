"""ARC.18 guard: every mutating service accepts `dry_run` (spec 01 §1.3, "Dry-run").

A public function of an `apps/<app>/services.py` module is *mutating* when it writes through the
ORM: it calls `save`, `delete`, `create`, `bulk_create`, `bulk_update`, `get_or_create`,
`update_or_create` (or their async forms), or `update` on a queryset, directly or through a
function of the same module. Such a service must take `dry_run`, or take the caller's
`ChangeSet` (it then belongs to the caller's dry run), or be one of the accepted exceptions
below.

The exceptions are the owner's list (decision Q-18): telemetry, throttle counters, one-time code
consumption and migration hooks. The same list is the "Dry-run exceptions" table of spec 01
§1.3; the last test keeps the two in step. The scan is a heuristic on the source: it catches
the usual write calls, and a reviewer still reads the rest.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "spec" / "01-stack-and-architecture.md"

TELEMETRY: Final = "telemetry"
THROTTLE: Final = "throttle counter"
ONE_TIME_CODE: Final = "one-time code"
MIGRATION_HOOK: Final = "migration hook"
CATEGORIES: Final = frozenset({TELEMETRY, THROTTLE, ONE_TIME_CODE, MIGRATION_HOOK})

# Mutating services without `dry_run`, with their category and the reason (spec 01 §1.3).
DRY_RUN_EXCEPTIONS: Final[dict[str, tuple[str, str]]] = {
    "apps.accounts.services.start_totp_enrolment": (
        ONE_TIME_CODE,
        "creates the pending device with a fresh secret; a preview would issue one too",
    ),
    "apps.accounts.services.confirm_totp_device": (
        ONE_TIME_CODE,
        "verifying the code consumes it",
    ),
    "apps.accounts.services.use_recovery_code": (
        ONE_TIME_CODE,
        "verifying a recovery code consumes it",
    ),
    "apps.accounts.services.register_passkey": (
        ONE_TIME_CODE,
        "stores a registration whose one-time WebAuthn challenge is already spent",
    ),
    "apps.accounts.services.authenticate_passkey": (
        ONE_TIME_CODE,
        "spends the one-time WebAuthn challenge and advances the signature counter",
    ),
    "apps.api.services.authenticate": (
        TELEMETRY,
        "records when an API key was last used",
    ),
    "apps.cms.services.sync_locales": (
        MIGRATION_HOOK,
        "post-migrate handler that adds the missing Wagtail locales",
    ),
    "apps.cms.services.seed_site": (
        MIGRATION_HOOK,
        "idempotent setup seed (`seed_cms`) that only adds missing sample pages",
    ),
    "apps.core.services.record_heartbeat": (
        TELEMETRY,
        "operational heartbeat of a scheduled job",
    ),
    "apps.core.services.record_throttle_failure": (
        THROTTLE,
        "counts one failed attempt",
    ),
    "apps.core.services.clear_throttle": (
        THROTTLE,
        "forgets the failures of a key after a success",
    ),
    "apps.core.services.purge_expired_throttles": (
        THROTTLE,
        "deletes expired counters",
    ),
    "apps.tax.services.record_vat_validation": (
        TELEMETRY,
        "append-only record of a VIES answer",
    ),
    "apps.tax.services.verify_vat_number": (
        TELEMETRY,
        "asking VIES stores its answer in the append-only validation log; a check is a lookup",
    ),
    "apps.tax.services.recheck_vat_number": (
        TELEMETRY,
        "same lookup as verify_vat_number, forced past the cache by the background job",
    ),
}

_WRITES: Final = (
    "save",
    "delete",
    "create",
    "bulk_create",
    "bulk_update",
    "get_or_create",
    "update_or_create",
)
WRITE_METHODS: Final = frozenset({*_WRITES, *(f"a{m}" for m in _WRITES)})
QUERYSET_UPDATES: Final = frozenset({"update", "aupdate"})

Function = ast.FunctionDef | ast.AsyncFunctionDef


def is_orm_write(call: ast.Call) -> bool:
    """A call that writes through the ORM. `update` counts only on a queryset expression
    (`Model.objects.update(...)`, `qs.filter(...).update(...)`), not on a dict or a ChangeSet."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in WRITE_METHODS:
        return not (isinstance(func.value, ast.Name) and func.value.id in {"changes", "changeset"})
    if func.attr in QUERYSET_UPDATES:
        receiver = func.value
        return isinstance(receiver, ast.Call) or (
            isinstance(receiver, ast.Attribute) and receiver.attr == "objects"
        )
    return False


def takes_dry_run(fn: Function) -> bool:
    args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    return any(
        arg.arg == "dry_run"
        or (arg.annotation is not None and "ChangeSet" in ast.unparse(arg.annotation))
        for arg in args
    )


def mutating_services(source: str) -> list[str]:
    """Public module-level functions of `source` that write through the ORM without `dry_run`."""
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, Function)}
    writes: dict[str, bool] = {}
    calls: dict[str, set[str]] = {}
    for name, fn in functions.items():
        nodes = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        writes[name] = any(is_orm_write(n) for n in nodes)
        calls[name] = {
            n.func.id for n in nodes if isinstance(n.func, ast.Name) and n.func.id in functions
        }

    def mutates(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if writes[name]:
            return True
        return any(mutates(c, seen | {name}) for c in calls[name] - seen - {name})

    return [
        name
        for name, fn in functions.items()
        if not name.startswith("_") and mutates(name) and not takes_dry_run(fn)
    ]


def service_files() -> Iterator[tuple[str, Path]]:
    for path in sorted((ROOT / "apps").glob("*/services.py")):
        yield f"apps.{path.parent.name}.services", path
    for path in sorted((ROOT / "apps").glob("*/services/*.py")):
        yield f"apps.{path.parent.parent.name}.services.{path.stem}", path


def public_functions(source: str) -> dict[str, Function]:
    return {
        n.name: n
        for n in ast.parse(source).body
        if isinstance(n, Function) and not n.name.startswith("_")
    }


@pytest.mark.parametrize(
    "source",
    [
        "def add(x):\n    x.save()",
        "def wipe():\n    Model.objects.filter(a=1).delete()",
        "def touch():\n    Model.objects.filter(pk=1).update(seen=True)",
        "def bump():\n    Model.objects.update(n=1)",
        "async def make():\n    await Model.objects.acreate()",
        "def outer():\n    _inner()\ndef _inner():\n    Model.objects.get_or_create(a=1)",
        "def change(x, *, dry_run_flag=False):\n    x.save()",
    ],
)
def test_scanner_flags_a_write_without_dry_run(source: str) -> None:
    assert mutating_services(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "def add(x, *, dry_run=False):\n    x.save()",
        "def add(x, dry_run):\n    _write(x)\ndef _write(x):\n    x.save()",
        "def hook(vendor, changes: ChangeSet):\n    Model.objects.filter(v=vendor).delete()",
        "def _private(x):\n    x.save()",
        "def snapshot(x):\n    data = {}\n    data.update(a=x)\n    return data",
        "def plan(changes):\n    changes.delete('m', 'k', {})",
        "def read():\n    return Model.objects.filter(a=1).first()",
    ],
)
def test_scanner_ignores_dry_run_services_reads_and_non_orm_calls(source: str) -> None:
    assert mutating_services(source) == []


def test_scan_covers_the_service_modules() -> None:
    modules = [module for module, _ in service_files()]
    assert "apps.core.services" in modules
    assert "apps.catalog.services" in modules


def test_every_mutating_service_accepts_dry_run_or_is_an_accepted_exception() -> None:
    offenders = [
        f"{module}.{name}"
        for module, path in service_files()
        for name in mutating_services(path.read_text(encoding="utf-8"))
        if f"{module}.{name}" not in DRY_RUN_EXCEPTIONS
    ]
    assert offenders == [], (
        "Mutating services without dry_run (add `dry_run=True` support returning a ChangeSet, "
        "or, for telemetry, throttle counters, one-time codes or migration hooks, list them in "
        "spec 01 §1.3 and in DRY_RUN_EXCEPTIONS): " + ", ".join(offenders)
    )


def test_every_exception_names_an_existing_service_without_dry_run() -> None:
    functions = {
        f"{module}.{name}": fn
        for module, path in service_files()
        for name, fn in public_functions(path.read_text(encoding="utf-8")).items()
    }
    stale = [name for name in DRY_RUN_EXCEPTIONS if name not in functions]
    with_dry_run = [name for name in DRY_RUN_EXCEPTIONS if name in functions]
    with_dry_run = [name for name in with_dry_run if takes_dry_run(functions[name])]
    assert stale == [], f"Exceptions naming no service: {stale}"
    assert with_dry_run == [], f"Exceptions that now accept dry_run (remove them): {with_dry_run}"


def test_every_exception_has_an_accepted_category_and_a_reason() -> None:
    for name, (category, reason) in DRY_RUN_EXCEPTIONS.items():
        assert category in CATEGORIES, f"{name}: category {category!r} is not in {CATEGORIES}"
        assert reason.strip(), f"{name}: give the reason"


SPEC_ROW = re.compile(r"^\|\s*`(apps\.[\w.]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")


def spec_exceptions() -> dict[str, str]:
    """Service -> category from the "Dry-run exceptions" table of spec 01 §1.3."""
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("### Dry-run exceptions")
    end = text.index("\n#", start + 1)
    rows: dict[str, str] = {}
    for line in text[start:end].splitlines():
        match = SPEC_ROW.match(line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def test_spec_lists_the_same_exceptions() -> None:
    assert spec_exceptions() == {
        name: category for name, (category, _) in DRY_RUN_EXCEPTIONS.items()
    }
