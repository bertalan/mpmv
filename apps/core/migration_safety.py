"""Keep database-backed system checks from blocking `migrate` (CORE.27).

`manage.py migrate` runs the `database`-tagged checks before it applies anything. On a database
from the previous release the tables, columns or seeded rows a check reads may not exist yet, and
the migration that creates them would then be stopped by the check (the v0.1.6 deploy blocker,
#269 and #274). Every check that queries the ORM therefore skips while the apps it reads still
have unapplied migrations. Once the app is fully migrated, `manage.py check --database default`
and `check --deploy` (run by the deploy script after `migrate`) run it for real.

Use `migrations_pending` inside a check, or `skip_while_pending` as a decorator when the whole
check needs the database.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.core import checks
from django.db import DEFAULT_DB_ALIAS, connections

CheckFunc = Callable[..., list[checks.CheckMessage]]


def migrations_pending(*app_labels: str, database: str = DEFAULT_DB_ALIAS) -> bool:
    """True while any migration of one of the apps is unapplied on `database`.

    Every node of the app counts, not only the leaf: a missing early migration (a seed) is as
    pending as a missing late one. An app with no migrations is never pending, and a database
    without the migration table counts as nothing applied.
    """
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connections[database], ignore_no_migrations=True)
    applied = loader.applied_migrations
    return any(
        key not in applied
        for key in loader.graph.nodes
        if key[0] in app_labels and key not in loader.replacements
    )


def skip_while_pending(*app_labels: str) -> Callable[[CheckFunc], CheckFunc]:
    """Decorator: the check returns no message while an app of `app_labels` has pending migrations.

    The database checked is the first of the `databases` argument Django passes, else `default`.
    """

    def decorate(func: CheckFunc) -> CheckFunc:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> list[checks.CheckMessage]:
            databases = kwargs.get("databases") or [DEFAULT_DB_ALIAS]
            if any(migrations_pending(*app_labels, database=alias) for alias in databases):
                return []
            return func(*args, **kwargs)

        return wrapper

    return decorate
