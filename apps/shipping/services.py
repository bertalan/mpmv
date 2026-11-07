"""Shipping services: audited writes of the shipping configuration (SHP.1).

Carriers, zones, vendor rates, weight bands and pickup options are prices or decide them, so
every create, change and delete goes through here, whatever the adapter (admin, the SHP.5
import, the SHP.6 editor): it is validated, returned as a ChangeSet (dry-run) and written to
the audit log with before and after (spec 01 conventions, docs/dev/audit-trail.md).
"""

from collections.abc import Iterable
from typing import Any

from django.db import models, transaction

from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation, save_audited

from .models import Carrier, PickupOption, ShippingZone, VendorZoneRate, WeightBand

type ConfigRow = Carrier | ShippingZone | VendorZoneRate | WeightBand | PickupOption

# Deletion order: rows that others point to come last (rates protect zones and carriers).
CONFIG_MODELS: tuple[type[models.Model], ...] = (
    WeightBand,
    PickupOption,
    VendorZoneRate,
    ShippingZone,
    Carrier,
)
NOT_SNAPSHOTTED = frozenset({"id", "public_id", "created_at", "updated_at"})


def _label(obj: models.Model) -> str:
    return obj._meta.label_lower


def _check(obj: object) -> None:
    if type(obj) not in CONFIG_MODELS:
        raise TypeError(f"Not an audited shipping configuration row: {type(obj).__name__}")


def _key(obj: models.Model) -> str:
    return str(obj.public_id)  # type: ignore[attr-defined]


def config_snapshot(obj: models.Model) -> dict[str, Any]:
    """The stored fields of a row; related rows by public id, plus the owning vendor."""
    data: dict[str, Any] = {}
    for field in obj._meta.concrete_fields:
        if field.name in NOT_SNAPSHOTTED:
            continue
        if field.is_relation:
            related = getattr(obj, field.name)
            data[field.name] = None if related is None else str(related.public_id)
        else:
            data[field.name] = field.value_from_object(obj)
    if isinstance(obj, WeightBand):
        data[VENDOR_FIELD] = str(obj.rate.vendor.public_id)
    return data


def save_config(obj: ConfigRow, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update one configuration row, audited as `shipping.<model>.created|updated`.

    The row is validated with its constraints (bands that overlap, a second carrier for a
    vendor and zone, negative amounts) before anything is written, so a dry run refuses what
    the database would. An update is computed against the stored row, never the edited one.
    """
    _check(obj)
    obj.full_clean()
    label = _label(obj)
    return save_audited(
        obj,
        config_snapshot,
        key=_key(obj),
        ctx=ctx,
        created=f"{label}.created",
        updated=f"{label}.updated",
        dry_run=dry_run,
    )


def delete_config(
    rows: Iterable[ConfigRow], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Delete configuration rows, audited as `shipping.<model>.deleted`.

    Deleting a rate deletes its weight bands: each band is audited too, so no price leaves the
    configuration without a trace.
    """
    targets = list(rows)
    for obj in targets:
        _check(obj)
    rate_ids = [obj.pk for obj in targets if isinstance(obj, VendorZoneRate)]
    cascaded = WeightBand.objects.filter(rate_id__in=rate_ids).select_related("rate__vendor")
    unique = {(type(obj), obj.pk): obj for obj in [*cascaded, *targets]}

    changes = ChangeSet()
    with transaction.atomic():
        for model in CONFIG_MODELS:
            group = [obj for (kind, _), obj in unique.items() if kind is model]
            if not group:
                continue
            changeset = ChangeSet()
            for obj in group:
                changeset.delete(_label(obj), _key(obj), config_snapshot(obj))

            def apply(group: list[ConfigRow] = group) -> None:
                for obj in group:
                    obj.delete()

            action = f"{model._meta.label_lower}.deleted"
            run_mutation(changeset, apply, ctx=ctx, action=action, dry_run=dry_run)
            changes.changes.extend(changeset.changes)
    return changes
