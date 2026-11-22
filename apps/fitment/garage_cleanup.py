"""The 24-month inactivity cleanup of account garage profiles (FIT.5 3/3, spec 17 §17.4.5).

A profile unused for the scheme's retention (default 24 months) is deleted, after a service email
the scheme's notice days before (default 30). The date rules
are pure (`engine.cleanup_action`); this module reads the rows, queues the email through the
outbox and deletes. The notice mail is one per customer, listing the profiles concerned. Both
steps take `dry_run` (the `ChangeSet` comes back, nothing is written, no email is queued) and
write `AuditLog` entries that hold the node id, the year and the flag, never the profile name.
Run nightly by `apps.fitment.tasks`; a rerun changes nothing new.
"""

import datetime as dt
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import enqueue_email, run_mutation

from . import engine
from .garage_profiles import MODEL, _snapshot
from .models import GarageProfile
from .services import current_scheme

PROFILE_NOTICED = "fitment.garage_profile.notice_sent"
PROFILE_EXPIRED = "fitment.garage_profile.expired"
NOTICE_TEMPLATE = "fitment.garage_inactivity_notice"
# A month has at least 28 days: a cheap pre-filter for the query.
_MIN_MONTH_DAYS = 28


def run_inactivity_cleanup(
    *, ctx: AuditContext, dry_run: bool = False, now: dt.datetime | None = None
) -> ChangeSet:
    stamp = now or timezone.now()
    scheme = current_scheme()
    months, notice_days = scheme.garage_inactivity_months, scheme.garage_notice_days
    earliest_due = dt.timedelta(days=max(months * _MIN_MONTH_DAYS - notice_days, 0))
    due = GarageProfile.objects.filter(
        last_used_at__lte=stamp - earliest_due, user__is_active=True
    ).select_related("node", "user")
    to_notify: dict[int, list[GarageProfile]] = defaultdict(list)
    to_delete: list[GarageProfile] = []
    for profile in due.order_by("user_id", "id"):
        action = engine.cleanup_action(
            profile.last_used_at,
            profile.inactivity_notice_at,
            stamp,
            months=months,
            notice_days=notice_days,
        )
        if action == engine.NOTIFY:
            to_notify[profile.user_id].append(profile)
        elif action == engine.DELETE:
            to_delete.append(profile)
    result = ChangeSet()
    for profiles in to_notify.values():
        result.changes.extend(
            _notify(
                profiles,
                ctx=ctx,
                dry_run=dry_run,
                now=stamp,
                months=months,
                notice_days=notice_days,
            ).changes
        )
    for profile in to_delete:
        changeset = ChangeSet()
        changeset.delete(MODEL, str(profile.public_id), _snapshot(profile))
        result.changes.extend(
            run_mutation(
                changeset, profile.delete, ctx=ctx, action=PROFILE_EXPIRED, dry_run=dry_run
            ).changes
        )
    return result


def _notify(
    profiles: list[GarageProfile],
    *,
    ctx: AuditContext,
    dry_run: bool,
    now: dt.datetime,
    months: int,
    notice_days: int,
) -> ChangeSet:
    user: User = profiles[0].user
    changeset = ChangeSet()
    for profile in profiles:
        changeset.update(
            MODEL,
            str(profile.public_id),
            {"notice_sent": False},
            {"notice_sent": True},
        )
    # Deletion needs the limit and the full notice period; the mail names the later date.
    delete_on = max(
        max(engine.add_months(p.last_used_at, months) for p in profiles),
        now + dt.timedelta(days=notice_days),
    )

    def apply() -> None:
        GarageProfile.objects.filter(pk__in=[p.pk for p in profiles]).update(
            inactivity_notice_at=now
        )
        enqueue_email(
            recipient=user.email,
            template_key=NOTICE_TEMPLATE,
            language=user.preferred_language,
            idempotency_key=f"fitment:garage-notice:{user.public_id}:{now.date().isoformat()}",
            context={
                "customer_name": user.full_name,
                "profiles": [p.name for p in profiles],
                "delete_on": delete_on.date().isoformat(),
            },
        )

    with transaction.atomic():
        return run_mutation(changeset, apply, ctx=ctx, action=PROFILE_NOTICED, dry_run=dry_run)
