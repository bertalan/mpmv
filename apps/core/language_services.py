"""Applied site languages and their changes (I18N.15, spec 09). I/O side of `language_engine`.

`change_languages` is the only way to remove a language or change the primary one: it records
the new set, creates the prefix redirects and audits every change. Adding a language is a
setting; the post-migrate hook `sync_language_state` records it.
"""

from collections.abc import Sequence

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils.http import url_has_allowed_host_and_scheme

from .audit import AuditContext
from .changeset import ChangeSet
from .language_engine import LanguagePlan, plan_language_change, settings_conflict
from .models import LanguageRedirect, SiteLanguages
from .services import run_mutation
from .sitemap_version import bump_sitemap_version

CHANGE_ACTION = "language.change"
STATE_KEY = "site"


def configured_codes() -> list[str]:
    return [str(code) for code, _name in settings.LANGUAGES]


def recorded_codes(using: str = DEFAULT_DB_ALIAS) -> list[str]:
    """The applied languages; empty when nothing is recorded yet."""
    row = SiteLanguages.objects.using(using).first()
    return [str(code) for code in row.codes] if row else []


def removed_codes(using: str = DEFAULT_DB_ALIAS) -> list[str]:
    """Languages `change_languages` removed and settings must not list again."""
    row = SiteLanguages.objects.using(using).first()
    return [str(code) for code in row.removed] if row else []


def current_language_codes() -> list[str]:
    """The applied languages, or the configured ones before the first record."""
    return recorded_codes() or configured_codes()


def _snapshot(codes: Sequence[str]) -> dict[str, object]:
    return {"codes": list(codes), "primary": codes[0] if codes else ""}


def sync_language_state(using: str = DEFAULT_DB_ALIAS, **kwargs: object) -> None:
    """Record the configured languages after a migration (first run, or added languages).

    A removal or a primary change in settings is not recorded: `core.E914` refuses it until
    `change_languages` has been run.
    """
    configured = configured_codes()
    row = SiteLanguages.objects.using(using).first()
    if row is None:
        SiteLanguages.objects.using(using).create(codes=configured)
    elif row.codes != configured and settings_conflict(row.codes, configured, row.removed) is None:
        row.codes = configured
        row.save(using=using)


def change_languages(target: Sequence[str], *, ctx: AuditContext, dry_run: bool) -> ChangeSet:
    """Make `target` (ordered, first = primary) the applied languages, with redirects."""
    plan, changes = _plan(target)
    if changes.is_empty:
        return changes

    def apply() -> None:
        row, _created = SiteLanguages.objects.get_or_create(defaults={"codes": list(plan.new)})
        gone = [code for code in plan.old if code not in plan.new]
        row.removed = [c for c in dict.fromkeys([*row.removed, *gone]) if c not in plan.new]
        row.codes = list(plan.new)
        row.save()
        bump_sitemap_version()
        LanguageRedirect.objects.filter(
            old_prefix__in=[_prefix(code, plan) for code in plan.new]
        ).delete()
        for old, new in plan.redirects:
            LanguageRedirect.objects.update_or_create(old_prefix=old, defaults={"new_prefix": new})

    with transaction.atomic():
        return run_mutation(changes, apply, ctx=ctx, action=CHANGE_ACTION, dry_run=dry_run)


def _prefix(code: str, plan: LanguagePlan) -> str:
    return "/" if code == plan.new[0] else f"/{code}/"


def plan_for(target: Sequence[str]) -> LanguagePlan:
    return plan_language_change(current_language_codes(), target)


def _plan(target: Sequence[str]) -> tuple[LanguagePlan, ChangeSet]:
    plan = plan_for(target)
    changes = ChangeSet()
    if plan.is_empty:
        return plan, changes
    changes.update("core.sitelanguages", STATE_KEY, _snapshot(plan.old), _snapshot(plan.new))
    stale = LanguageRedirect.objects.filter(
        old_prefix__in=[_prefix(code, plan) for code in plan.new]
    )
    for redirect in stale:
        changes.delete(
            "core.languageredirect",
            redirect.old_prefix,
            {"old_prefix": redirect.old_prefix, "new_prefix": redirect.new_prefix},
        )
    existing = {r.old_prefix: r.new_prefix for r in LanguageRedirect.objects.all()}
    for old, new in plan.redirects:
        after = {"old_prefix": old, "new_prefix": new}
        if old not in existing:
            changes.create("core.languageredirect", old, after)
        elif existing[old] != new:
            changes.update(
                "core.languageredirect",
                old,
                {"old_prefix": old, "new_prefix": existing[old]},
                after,
            )
    return plan, changes


def redirect_target(path: str) -> str | None:
    """Where a path under a retired language prefix goes; None when no redirect matches."""
    rows = sorted(LanguageRedirect.objects.all(), key=lambda r: len(r.old_prefix), reverse=True)
    for row in rows:
        if path.startswith(row.old_prefix):
            remainder = path[len(row.old_prefix) :].lstrip("/\\")
            target = row.new_prefix + remainder
        elif path == row.old_prefix.rstrip("/"):
            target = row.new_prefix
        else:
            continue
        return target if _is_local(target) else None
    return None


def _is_local(target: str) -> bool:
    """A same-site path: one leading slash, no backslash, no scheme or host (open redirects)."""
    return (
        target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
        and url_has_allowed_host_and_scheme(target, allowed_hosts=None)
    )
