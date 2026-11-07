"""Shipping services: audited writes of the shipping configuration (SHP.1, SHP.12), and the zone
snapshots the pure zone resolution works on.

Carriers, country groups, zones, shipping methods, weight bands and pickup options are prices
or decide them, so every create, change and delete goes through here, whatever the adapter
(admin, the SHP.5 and SHP.13 imports, the SHP.6 editor): it is validated, returned as a ChangeSet
(dry-run) and written to the audit log with before and after (spec 01 conventions,
docs/dev/audit-trail.md).
"""

from collections.abc import Iterable
from typing import Any

from django.db import models, transaction
from django.db.models import ProtectedError, Q

from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation, save_audited
from apps.vendors.models import Vendor

from .engine import ZoneSnap
from .models import (
    Carrier,
    CountryGroup,
    PickupOption,
    ShippingMethod,
    ShippingZone,
    WeightBand,
    zones_using_groups,
)

type ConfigRow = Carrier | CountryGroup | ShippingZone | ShippingMethod | WeightBand | PickupOption

# Deletion order: rows that others point to come last (methods protect zones and carriers; zones
# name country groups).
CONFIG_MODELS: tuple[type[models.Model], ...] = (
    WeightBand,
    PickupOption,
    ShippingMethod,
    ShippingZone,
    CountryGroup,
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
        data[VENDOR_FIELD] = str(obj.method.vendor.public_id)
    return data


def save_config(obj: ConfigRow, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Create or update one configuration row, audited as `shipping.<model>.created|updated`.

    The row is validated with its constraints and cross-table rules (bands that overlap, a
    second default method for a vendor and zone, another vendor's zone, unknown country groups,
    negative amounts) before anything is written, so a dry run refuses what the database would.
    An update is computed against the stored row, never the edited one.
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


def _refuse_groups_in_use(targets: list[ConfigRow]) -> None:
    """Zones name country groups by code, so the database cannot protect them: refuse here."""
    codes = [obj.code for obj in targets if isinstance(obj, CountryGroup)]
    if not codes:
        return
    deleted_zones = [obj.pk for obj in targets if isinstance(obj, ShippingZone)]
    users = zones_using_groups(codes).exclude(pk__in=deleted_zones)
    if users.exists():
        raise ProtectedError(
            f"Zones use the country groups {', '.join(sorted(codes))}: change them first.",
            set(users),
        )


def delete_config(
    rows: Iterable[ConfigRow], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Delete configuration rows, audited as `shipping.<model>.deleted`.

    Deleting a method deletes its weight bands: each band is audited too, so no price leaves the
    configuration without a trace. A country group that a remaining zone uses is refused with
    `ProtectedError`, also on dry-run.
    """
    targets = list(rows)
    for obj in targets:
        _check(obj)
    _refuse_groups_in_use(targets)
    method_ids = [obj.pk for obj in targets if isinstance(obj, ShippingMethod)]
    cascaded = WeightBand.objects.filter(method_id__in=method_ids).select_related("method__vendor")
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


# --- Zones a vendor can use, as snapshots for `engine.resolve_zone`


def zones_available_to(vendor: Vendor) -> "models.QuerySet[ShippingZone]":
    """The platform zones and `vendor`'s own zones: never another vendor's."""
    return ShippingZone.objects.filter(Q(vendor__isnull=True) | Q(vendor=vendor))


def zone_snaps(vendor: Vendor) -> list[ZoneSnap]:
    """Snapshots of the zones `vendor` ships to (it has a method there), groups expanded."""
    zones = list(zones_available_to(vendor).filter(methods__vendor=vendor).distinct())
    codes = {code for zone in zones for code in zone.country_groups}
    members = {
        group.code: frozenset(group.countries)
        for group in CountryGroup.objects.filter(code__in=codes)
    }
    return [
        ZoneSnap(
            code=zone.code,
            priority=zone.priority,
            countries=frozenset(zone.countries),
            group_countries=frozenset().union(
                *(members.get(code, frozenset()) for code in zone.country_groups)
            ),
            postcode_patterns=tuple(zone.postcode_patterns),
            states=tuple(zone.states),
            vendor_id=zone.vendor_id,
        )
        for zone in zones
    ]
