"""SEC.4 guard: every sensitive mutation writes the AuditLog (spec 01 conventions, spec 12 SEC.7).

Two structural checks keep new code from skipping the audit trail:

- every model of our apps, and every model in the staff admin, is classified as audited or
  exempt (with a reason), and audited models are written only through audited admin methods
  (the `core.E2xx` system check, also run by `make check`);
- every mutating service (one that offers `dry_run`) takes the `AuditContext` it audits with,
  unless it is listed below with the reason it is not sensitive.
"""

import importlib
import inspect
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

from django.apps import apps

from apps.core.checks import check_audit_policy

# Mutating services that are deliberately not audited, with the reason.
NOT_SENSITIVE_SERVICES = {
    "apps.accounts.services.set_preferred_language": "a display preference, not a security fact",
    "apps.api.services.record_mutation_call": "logs the API call itself; `dry_run` is a field",
    "apps.core.services.enqueue_email": "the outbox is a delivery queue with its own history",
    "apps.core.services.deliver_email": "a delivery attempt, kept in the outbox history",
}


def service_modules() -> Iterator[ModuleType]:
    for config in apps.get_app_configs():
        if not config.name.startswith("apps."):
            continue
        try:
            yield importlib.import_module(f"{config.name}.services")
        except ModuleNotFoundError as exc:
            if exc.name != f"{config.name}.services":
                raise


def public_functions(module: ModuleType) -> Iterator[tuple[str, Callable[..., Any]]]:
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("_") and function.__module__ == module.__name__:
            yield f"{module.__name__}.{name}", function


def test_the_project_passes_the_audit_policy_check() -> None:
    assert check_audit_policy(None) == []


def test_every_mutating_service_takes_an_audit_context() -> None:
    missing = [
        path
        for module in service_modules()
        for path, function in public_functions(module)
        if "dry_run" in inspect.signature(function).parameters
        and "ctx" not in inspect.signature(function).parameters
        and path not in NOT_SENSITIVE_SERVICES
    ]
    assert missing == [], f"mutating services without an AuditContext: {missing}"


def test_the_exemptions_name_real_services() -> None:
    known = {path for module in service_modules() for path, _ in public_functions(module)}
    assert set(NOT_SENSITIVE_SERVICES) <= known
