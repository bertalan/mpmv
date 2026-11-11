"""The Manager URL sweep and system checks (CORE.17, spec 02 §Backoffice "plus a sweep").

`manager_route_problems` walks a URLconf and reports every route registered under `/manager/`
that is not under exactly one gated space prefix, is a regex (whose reach cannot be read), or
whose view does not name a registered section of that space. The unit suite asserts the project
has none, and `manage.py check` fails on any (manager.E001), so a view cannot be added ungated.
Catch-all routes outside `/manager/` (the CMS pages) are the gate's business: it answers 404 to
every path under `/manager/` outside both spaces.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from django.conf import settings
from django.core import checks
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.resolvers import RegexPattern

from .navigation import registry, section_key
from .spaces import MANAGER_PREFIX, Space, space_of

GATE = "apps.manager.middleware.ManagerGateMiddleware"
_MANAGER_ROUTE = MANAGER_PREFIX.removeprefix("/")  # "manager/"
_REGEX_SYNTAX = re.compile(r"[.^$*+?{}\[\]\\|()]")

type UrlConf = str | Sequence[URLPattern | URLResolver] | None


def _text(part: Any) -> tuple[str, str, bool]:
    """One pattern as (text, literal start, is_regex)."""
    if isinstance(part, RegexPattern):
        text = str(part).removeprefix("^").removesuffix("$")
        cut = _REGEX_SYNTAX.search(text)
        return text, (text[: cut.start()] if cut else text), True
    text = str(part)
    return text, text.split("<", 1)[0], False


def _walk(
    patterns: Sequence[URLPattern | URLResolver], parts: tuple[Any, ...] = ()
) -> Iterator[tuple[tuple[Any, ...], Callable[..., Any]]]:
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, (*parts, pattern.pattern))
        else:
            yield (*parts, pattern.pattern), pattern.callback


def _patterns(urlconf: UrlConf) -> Sequence[URLPattern | URLResolver]:
    if urlconf is None or isinstance(urlconf, str):
        return get_resolver(urlconf).url_patterns
    return urlconf


def _manager_leaves(urlconf: UrlConf) -> Iterator[tuple[str, bool, Callable[..., Any]]]:
    """(route, is_regex, view) of every route whose literal start is under `/manager/`."""
    for parts, callback in _walk(_patterns(urlconf)):
        route, literal, is_regex, open_ended = "", "", False, False
        for part in parts:
            text, start, regex = _text(part)
            route += text
            is_regex = is_regex or regex
            if not open_ended:
                literal += start
                open_ended = start != text
        if literal.startswith(_MANAGER_ROUTE) or literal == _MANAGER_ROUTE.rstrip("/"):
            yield route, is_regex, callback


def manager_routes(urlconf: UrlConf = None) -> list[tuple[str, Space | None]]:
    """Every route under `/manager/` with its space (None outside both)."""
    return [(route, space_of("/" + route)) for route, _, _ in _manager_leaves(urlconf)]


def manager_route_problems(urlconf: UrlConf = None) -> list[str]:
    problems = []
    for route, is_regex, callback in _manager_leaves(urlconf):
        space = space_of("/" + route)
        if is_regex:
            problems.append(f"/{route}: a regex route under the Manager; use path() under a space")
            continue
        if space is None:
            problems.append(f"/{route}: not under exactly one gated prefix (shop/ or platform/)")
            continue
        key = section_key(callback)
        section = registry.get(key) if key else None
        if section is None:
            problems.append(f"/{route}: the view names no registered Manager section")
        elif section.space is not space:
            problems.append(
                f"/{route}: the view's section {section.key!r} belongs to the {section.space} "
                f"space, not {space}"
            )
    return problems


def check_manager_urls(app_configs: object = None, **kwargs: object) -> list[checks.CheckMessage]:
    return [
        checks.Error(problem, hint="Mount Manager views under a space prefix.", id="manager.E001")
        for problem in manager_route_problems()
    ]


def check_gate_installed(app_configs: object = None, **kwargs: object) -> list[checks.CheckMessage]:
    middleware: list[str] = list(settings.MIDDLEWARE)
    after = "apps.accounts.middleware.MfaRequiredMiddleware"
    if GATE not in middleware or (
        after in middleware and middleware.index(GATE) < middleware.index(after)
    ):
        return [
            checks.Error(
                f"{GATE} must be in MIDDLEWARE, after the authentication and MFA middlewares.",
                id="manager.E002",
            )
        ]
    return []


def matrix_problems() -> list[str]:
    """Drift of the matrix from the code: static, no database (manager.E003)."""
    from django.apps import apps

    from . import roles

    problems = []
    for section in registry.all():
        if section.space is Space.PLATFORM and section.key not in roles.MATRIX:
            problems.append(f"Platform section {section.key!r} has no row in roles.MATRIX.")
    for key, cells in roles.MATRIX.items():
        if key not in roles.SECTION_LABELS:
            problems.append(f"Matrix row {key!r} has no label.")
        for role_key in cells:
            if role_key not in (*roles.ROLE_KEYS, roles.SUPERUSER):
                problems.append(f"Matrix row {key!r} names the unknown role {role_key!r}.")
        if roles.SUPERUSER in cells and key not in roles.BREAK_GLASS_SECTIONS:
            problems.append(f"Row {key!r} gives the technical superuser more than break-glass.")
    for r in roles.ROLES:
        for label in r.permissions:
            app_label, codename = label.split(".")
            try:
                model_perms = {
                    p
                    for model in apps.get_app_config(app_label).get_models()
                    for p in _model_codenames(model)
                }
            except LookupError:
                model_perms = set()
            if codename not in model_perms:
                problems.append(f"Role {r.key!r} names {label}, which no model declares.")
    return problems


def _model_codenames(model: Any) -> Iterator[str]:
    meta = model._meta
    for action in meta.default_permissions:
        yield f"{action}_{meta.model_name}"
    for codename, _name in meta.permissions:
        yield codename


def check_roles_matrix(app_configs: object = None, **kwargs: object) -> list[checks.CheckMessage]:
    return [
        checks.Error(problem, hint="Fix apps/manager/roles.py.", id="manager.E003")
        for problem in matrix_problems()
    ]


SEED_MIGRATION = ("manager", "0002_seed_role_groups")  # first group seed; later ones follow


def _manager_migrations_pending(connection: Any) -> bool:
    """True while any manager migration (the group seeds) or any orders migration is unapplied."""
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    applied = executor.loader.applied_migrations
    if not any(app == "orders" for app, _ in applied):
        return True
    # Every manager node must be recorded, not only the leaf: a missing earlier one (the first
    # group seed) is as pending as a missing later one.
    return any(key not in applied for key in executor.loader.graph.nodes if key[0] == "manager")


def check_role_drift(
    app_configs: object = None, databases: Sequence[str] | None = None, **kwargs: object
) -> list[checks.CheckMessage]:
    """The role groups hold exactly the permissions of `roles.py` (manager.E004, E005).

    Needs the database: runs with `manage.py check --database default` and in the test suite.
    `migrate` runs the database checks before it applies anything, so the checks skip while a
    manager migration is unapplied (the seeds that create and update the groups, so drift against
    the current `roles.py` is expected) or the orders tables are missing. Every change to
    `roles.py` therefore needs a new sync migration.
    """
    from django.db import DatabaseError, connections

    from .role_groups import role_conflicts, role_drift

    if databases is not None and "default" not in databases:
        return []
    try:
        if _manager_migrations_pending(connections["default"]):
            return []
        problems, conflicts = role_drift(), role_conflicts()
    except DatabaseError:  # not migrated yet
        return []
    found: list[checks.CheckMessage] = []
    for problem in conflicts:
        found.append(
            checks.Error(
                problem,
                hint="Remove the user from the role group, or the vendor membership.",
                id="manager.E005",
            )
        )
    for problem in problems:
        found.append(
            checks.Error(
                problem,
                hint="Restore the group from apps/manager/roles.py (sync_role_groups), or change "
                "roles.py and add a data migration; do not edit role groups in the admin.",
                id="manager.E004",
            )
        )
    return found
