"""Export and import of the shipping configuration, and the standard template (SHP.13, spec 06).

Files use the format of `apps.shipping.config_format` (version 1, JSON or CSV). An import runs
in three steps, and an invalid file changes nothing:

1. shape: the format parser (`config_format.parse_json` / `parse_csv`);
2. rows: every row is built as an unsaved model instance and validated by the model itself
   (`clean_fields`, `clean` and its check constraints), the validators the admin and the tests
   use; then what only the whole file shows: references to carriers, zones and country groups,
   one default method per zone, weight bands that do not overlap (`engine.overlapping_bands`,
   the rule of the database constraint);
3. writes: in one transaction every row goes through `services.save_config` (full validation,
   the database constraints, one audit entry per row). When a row fails, the report names it
   and the transaction is rolled back. A dry run is rolled back as well, once the ChangeSet is
   complete, so the preview is exactly what the import would do.

Who owns a file: a vendor, or the platform (`vendor=None`).

- A vendor file writes the vendor's zones, methods, weight bands and pickup options. Its methods
  ship to the vendor's own zones (`zone_scope` own: in the file or already stored) or to platform
  zones (`zone_scope` platform). Country groups are platform data: the file's groups are
  compared with the platform's and never written (a missing group is an error, a different one
  a warning).
- A platform file writes country groups and platform zones; methods and pickup options belong to
  a vendor and are refused.
- Rows are matched by their names (codes and keys) and created or updated; rows missing from the
  file are kept. The weight bands of a method in the file replace its stored bands.

`tax_treatment` and `over_max_weight` travel as stored data only: their meaning belongs to TAX.4
(D-03) and SHP.3 (D-06).
"""

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, models, transaction

from apps.core.audit import VENDOR_FIELD, AuditContext, record
from apps.core.changeset import ChangeSet, to_jsonable
from apps.core.services import save_audited
from apps.vendors.models import Vendor

from . import config_format as fmt
from . import engine
from .models import (
    Carrier,
    CountryGroup,
    PickupOption,
    ShippingMethod,
    ShippingTemplate,
    ShippingZone,
    WeightBand,
)
from .services import ConfigRow, delete_config, save_config

CREATED: Final = "created"
UPDATED: Final = "updated"
UNCHANGED: Final = "unchanged"
SKIPPED: Final = "skipped"
ERROR: Final = "error"

IMPORTED: Final = "shipping.config.imported"
CONFIG_TARGET: Final = "shipping.config"
TEMPLATE_CREATED: Final = "shipping.shippingtemplate.created"
TEMPLATE_UPDATED: Final = "shipping.shippingtemplate.updated"
TEMPLATE_FIELDS: Final = ("code", "name", "is_standard", "document")

MODELS: Final[Mapping[str, type[models.Model]]] = {
    fmt.COUNTRY_GROUP: CountryGroup,
    fmt.ZONE: ShippingZone,
    fmt.METHOD: ShippingMethod,
    fmt.BAND: WeightBand,
    fmt.PICKUP_OPTION: PickupOption,
}
# Foreign keys: resolved from the file's names, never validated as raw values.
RELATIONS: Final = frozenset({"vendor", "zone", "carrier", "method"})
# Method fields that name its zone and carrier rather than store a value.
METHOD_LINKS: Final = frozenset({"zone_scope", "zone", "carrier"})
VENDOR_ROWS: Final = frozenset({fmt.METHOD, fmt.BAND, fmt.PICKUP_OPTION})


@dataclass
class RowReport:
    """What happened to one row of the file (or to the file itself, `ref` "file")."""

    ref: str
    record: str
    key: str
    status: str = UNCHANGED
    errors: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def fail(self, name: str, message: str) -> None:
        self.errors.setdefault(name, []).append(message)
        self.status = ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "record": self.record,
            "key": self.key,
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class ImportResult:
    rows: list[RowReport]
    changeset: ChangeSet = field(default_factory=ChangeSet)
    applied: bool = False

    @property
    def ok(self) -> bool:
        return not any(row.errors for row in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "applied": self.applied,
            "summary": self.changeset.summary(),
            "rows": [row.to_dict() for row in self.rows],
            "changes": self.changeset.to_dict()["changes"],
        }


class InvalidConfig(ValueError):
    """A configuration document that does not validate; `rows` is the per-row report."""

    def __init__(self, rows: list[RowReport]) -> None:
        self.rows = rows
        failed = sum(1 for row in rows if row.errors)
        super().__init__(f"The shipping configuration has errors in {failed} rows.")


# --- Export


def export_config(vendor: Vendor | None) -> dict[str, Any]:
    """The configuration of `vendor`, or of the platform (`None`), as a JSON-safe document.

    A vendor's export holds its zones, the country groups they use, its methods (on its zones or
    on platform zones) with their bands, and its pickup options. The platform's holds every
    country group and the platform zones. Rows are sorted, so equal configurations export equal.
    """
    document = fmt.empty_document()
    zones = ShippingZone.objects.filter(vendor=vendor).order_by("priority", "code")
    if vendor is None:
        groups = CountryGroup.objects.all()
        methods: list[ShippingMethod] = []
        pickups: list[PickupOption] = []
    else:
        used = {code for zone in zones for code in zone.country_groups}
        groups = CountryGroup.objects.filter(code__in=used)
        methods = list(
            ShippingMethod.objects.filter(vendor=vendor)
            .select_related("zone", "carrier")
            .prefetch_related("bands")
        )
        pickups = list(PickupOption.objects.filter(vendor=vendor).order_by("key"))
    document["country_groups"] = [_values(g, fmt.COUNTRY_GROUP) for g in groups.order_by("code")]
    document["zones"] = [_values(zone, fmt.ZONE) for zone in zones]
    document["methods"] = sorted(
        (_method_values(method) for method in methods),
        key=lambda item: (item["zone"], item["zone_scope"], item["key"]),
    )
    document["pickup_options"] = [_values(pickup, fmt.PICKUP_OPTION) for pickup in pickups]
    exported: dict[str, Any] = to_jsonable(document)
    return exported


def _values(obj: models.Model, record: str) -> dict[str, Any]:
    return {name: getattr(obj, name) for name in fmt.FIELDS[record]}


def _method_values(method: ShippingMethod) -> dict[str, Any]:
    links = {
        "zone_scope": fmt.PLATFORM if method.zone.vendor_id is None else fmt.OWN,
        "zone": method.zone.code,
        "carrier": method.carrier.code,
    }
    values = {
        name: links[name] if name in METHOD_LINKS else getattr(method, name)
        for name in fmt.FIELDS[fmt.METHOD]
    }
    bands = sorted(method.bands.all(), key=lambda band: band.from_kg)
    values[fmt.BANDS] = [_values(band, fmt.BAND) for band in bands]
    return values


# --- Import


def import_config(
    parsed: fmt.ParsedConfig,
    *,
    vendor: Vendor | None,
    ctx: AuditContext,
    dry_run: bool = False,
    source: str = "file",
) -> ImportResult:
    """Import a parsed file into the configuration of `vendor`, or of the platform (`None`).

    Returns the per-row report and the ChangeSet. Nothing is written when the file has an error
    or on a dry run. Each written row is audited by `save_config`; the import itself is audited
    as `shipping.config.imported` with its source and summary.
    """
    platform = vendor is None
    rows = check_rows(parsed, vendor=vendor, platform=platform)
    result = ImportResult(rows)
    if not result.ok:
        return result
    with transaction.atomic():
        _Writer(parsed, rows, vendor=vendor, ctx=ctx, changeset=result.changeset).write()
        if not result.ok or dry_run:
            transaction.set_rollback(True)
            return result
        if not result.changeset.is_empty:
            _audit_import(result.changeset, vendor=vendor, ctx=ctx, source=source)
        result.applied = True
    return result


def _audit_import(
    changeset: ChangeSet, *, vendor: Vendor | None, ctx: AuditContext, source: str
) -> None:
    after: dict[str, Any] = {
        "source": source,
        "format": fmt.FORMAT,
        "version": fmt.VERSION,
        "summary": changeset.summary(),
    }
    key = "platform"
    if vendor is not None:
        key = after[VENDOR_FIELD] = str(vendor.public_id)
    record(ctx, IMPORTED, CONFIG_TARGET, key, None, after)


def check_rows(
    parsed: fmt.ParsedConfig, *, vendor: Vendor | None, platform: bool
) -> list[RowReport]:
    """Steps 1 and 2: the report of the file's shape and of each row, without writing.

    `platform` checks a platform file; otherwise a vendor's (`vendor` set) or a template's
    (`vendor` None: its methods ship to the template's zones or to platform zones).
    """
    reports = [RowReport(row.ref, row.record, row.label) for row in parsed.rows]
    by_ref = {report.ref: report for report in reports}
    for issue in parsed.issues:
        report = by_ref.get(issue.ref)
        if report is None:
            report = by_ref[issue.ref] = RowReport(issue.ref, fmt.FILE, issue.ref)
            reports.append(report)
        report.fail(issue.field, issue.message)
    if not parsed.ok:
        return reports
    _Checker(parsed, reports, vendor=vendor, platform=platform).check()
    return reports


class _Checker:
    """Step 2: each row validated by its model, then the rules that span rows of the file."""

    def __init__(
        self,
        parsed: fmt.ParsedConfig,
        reports: list[RowReport],
        *,
        vendor: Vendor | None,
        platform: bool,
    ) -> None:
        self.rows = list(zip(parsed.rows, reports, strict=True))
        self.vendor = vendor
        self.platform = platform
        self.carriers = set(
            Carrier.objects.filter(
                code__in=[row.values.get("carrier") for row, _ in self.rows]
            ).values_list("code", flat=True)
        )
        self.file_zones = {row.values["code"] for row, _ in self.rows if row.record == fmt.ZONE}
        self.platform_zones = set(
            ShippingZone.objects.filter(vendor__isnull=True).values_list("code", flat=True)
        )
        self.vendor_zones: set[str] = set()
        if vendor is not None:
            self.vendor_zones = set(
                ShippingZone.objects.filter(vendor=vendor).values_list("code", flat=True)
            )
        self.defaults: dict[tuple[str, str], str] = {}
        self.bands: dict[fmt.MethodId, list[tuple[engine.Band, RowReport]]] = defaultdict(list)

    def check(self) -> None:
        for row, report in self.rows:
            if self.platform and row.record in VENDOR_ROWS:
                report.fail("vendor", f"A {row.record} belongs to a vendor: import it there.")
            elif row.record == fmt.COUNTRY_GROUP and not self.platform:
                _compare_group(row, report)
            else:
                self.check_row(row, report)
        for bands in self.bands.values():
            spans = [span for span, _ in bands]
            for i, j in engine.overlapping_bands(spans):
                lower, upper = spans[i]
                bands[j][1].fail(
                    "from_kg",
                    f"Overlaps the band from {lower} to {upper or 'unlimited'} kg of its method.",
                )

    def check_row(self, row: fmt.Row, report: RowReport) -> None:
        instance = MODELS[row.record](**_model_values(row))
        # A platform zone may use a group of the same file, written before it: its own rule
        # (`ShippingZone.clean`) then runs when the zone is written.
        _validate(instance, report, run_clean=not (self.platform and row.record == fmt.ZONE))
        if isinstance(instance, ShippingMethod):
            self.check_method(row, instance, report)
        elif isinstance(instance, WeightBand) and not report.errors and row.method is not None:
            self.bands[row.method].append(((instance.from_kg, instance.to_kg), report))

    def check_method(self, row: fmt.Row, method: ShippingMethod, report: RowReport) -> None:
        scope, zone, _ = fmt.method_id(row.values)
        if row.values.get("carrier") not in self.carriers:
            report.fail("carrier", f"Unknown carrier {row.values.get('carrier')!r}.")
        known = (
            self.platform_zones if scope == fmt.PLATFORM else self.file_zones | self.vendor_zones
        )
        if zone not in known:
            report.fail("zone", f"Unknown {scope} zone {zone!r}.")
        if method.is_default is True:
            first = self.defaults.setdefault((scope, zone), report.ref)
            if first != report.ref:
                report.fail("is_default", f"{first} is already the default method of the zone.")


def _model_values(row: fmt.Row) -> dict[str, Any]:
    if row.record == fmt.METHOD:
        return {k: v for k, v in row.values.items() if k not in METHOD_LINKS}
    return dict(row.values)


def _validate(instance: models.Model, report: RowReport, *, run_clean: bool = True) -> None:
    """The model's own validation of an unsaved row, without its relations and uniqueness
    (those need the stored rows: they are checked when the row is written)."""
    errors: dict[str, list[str]] = {}
    steps: list[Callable[[], None]] = [lambda: instance.clean_fields(exclude=RELATIONS)]
    if run_clean:
        steps.append(instance.clean)
    for step in steps:
        try:
            step()
        except ValidationError as exc:
            _merge(errors, exc)
    try:
        instance.validate_constraints(exclude={*RELATIONS, *errors})
    except ValidationError as exc:
        _merge(errors, exc)
    for name, messages in errors.items():
        for message in messages:
            report.fail(name, message)


def _merge(errors: dict[str, list[str]], exc: ValidationError) -> None:
    found = exc.message_dict if hasattr(exc, "error_dict") else {NON_FIELD_ERRORS: exc.messages}
    for name, messages in found.items():
        errors.setdefault(name, []).extend(messages)


def _compare_group(row: fmt.Row, report: RowReport) -> None:
    """A vendor file names platform groups; it never changes them."""
    code = str(row.values["code"]).strip().lower()
    stored = CountryGroup.objects.filter(code=code).first()
    if stored is None:
        report.fail("code", f"Unknown country group {code!r}: country groups are platform data.")
        return
    probe = CountryGroup(code=code, countries=row.values.get("countries", stored.countries))
    _validate(probe, report, run_clean=False)
    if not report.errors and sorted(probe.countries) != sorted(stored.countries):
        report.status = SKIPPED
        report.warnings.append(
            f"The platform group {code!r} has other countries; it is kept as it is."
        )


class _Writer:
    """Step 3: writes each row through `save_config`, in a savepoint so that the report can go
    on after a failing row; the caller rolls everything back when a row failed."""

    def __init__(
        self,
        parsed: fmt.ParsedConfig,
        reports: list[RowReport],
        *,
        vendor: Vendor | None,
        ctx: AuditContext,
        changeset: ChangeSet,
    ) -> None:
        self.rows = list(zip(parsed.rows, reports, strict=True))
        self.vendor = vendor
        self.ctx = ctx
        self.changeset = changeset
        self.methods: dict[fmt.MethodId, ShippingMethod | None] = {}

    def write(self) -> None:
        def kind(record: str) -> list[tuple[fmt.Row, RowReport]]:
            return [(row, report) for row, report in self.rows if row.record == record]

        if self.vendor is None:
            for row, report in kind(fmt.COUNTRY_GROUP):
                self.save(report, self.group(row))
        for row, report in kind(fmt.ZONE):
            self.save(report, self.zone(row))
        methods = kind(fmt.METHOD)
        # Non-default methods first, so that a file can move a zone's default to another method.
        for row, report in sorted(
            methods, key=lambda item: item[0].values.get("is_default") is True
        ):
            method = self.method(row, report)
            ok = method is not None and self.save(report, method)
            self.methods[fmt.method_id(row.values)] = method if ok else None
        for method_id, method in self.methods.items():
            bands = [item for item in kind(fmt.BAND) if item[0].method == method_id]
            self.write_bands(method, bands)
        for row, report in kind(fmt.PICKUP_OPTION):
            self.save(report, self.pickup(row))

    def save(self, report: RowReport, obj: ConfigRow) -> bool:
        try:
            with transaction.atomic():
                changes = save_config(obj, ctx=self.ctx)
        except ValidationError as exc:
            errors: dict[str, list[str]] = {}
            _merge(errors, exc)
            for name, messages in errors.items():
                for message in messages:
                    report.fail(name, message)
            return False
        except IntegrityError as exc:
            report.fail(NON_FIELD_ERRORS, str(exc).strip())
            return False
        if not changes.is_empty:
            report.status = CREATED if changes.changes[0].op == "create" else UPDATED
        self.changeset.changes.extend(changes.changes)
        return True

    def group(self, row: fmt.Row) -> CountryGroup:
        code = str(row.values["code"]).strip().lower()
        group = CountryGroup.objects.filter(code=code).first() or CountryGroup()
        return _assign(group, row.values)

    def zone(self, row: fmt.Row) -> ShippingZone:
        zone = ShippingZone.objects.filter(vendor=self.vendor, code=row.values["code"]).first()
        return _assign(zone or ShippingZone(vendor=self.vendor), row.values)

    def method(self, row: fmt.Row, report: RowReport) -> ShippingMethod | None:
        scope, code, key = fmt.method_id(row.values)
        zone_owner = None if scope == fmt.PLATFORM else self.vendor
        zone = ShippingZone.objects.filter(vendor=zone_owner, code=code).first()
        carrier = Carrier.objects.filter(code=row.values.get("carrier")).first()
        if zone is None or carrier is None:  # its zone row failed (checked before writing)
            report.fail("zone" if zone is None else "carrier", "Not available: see its row.")
            return None
        method = ShippingMethod.objects.filter(vendor=self.vendor, zone=zone, key=key).first()
        method = method or ShippingMethod(vendor=self.vendor, zone=zone, key=key)
        method.carrier = carrier
        return _assign(method, _model_values(row))

    def write_bands(
        self, method: ShippingMethod | None, bands: list[tuple[fmt.Row, RowReport]]
    ) -> None:
        """The file's bands replace the method's: stored bands are matched by `from_kg`, the
        unmatched ones deleted first (so the new bands cannot overlap them)."""
        if method is None:
            for _, report in bands:
                report.status = SKIPPED
                report.warnings.append("Not imported: its method has errors.")
            return
        stored = {band.from_kg: band for band in method.bands.all()}
        wanted = {_from_kg(row.values): (row, report) for row, report in bands}
        removed = [band for start, band in stored.items() if start not in wanted]
        if removed:
            self.changeset.changes.extend(delete_config(removed, ctx=self.ctx).changes)
        for start, (row, report) in wanted.items():
            band = stored.get(start) or WeightBand(method=method)
            self.save(report, _assign(band, row.values))

    def pickup(self, row: fmt.Row) -> PickupOption:
        pickup = PickupOption.objects.filter(vendor=self.vendor, key=row.values["key"]).first()
        return _assign(pickup or PickupOption(vendor=self.vendor), row.values)


def _from_kg(values: Mapping[str, Any]) -> Decimal:
    model_field = WeightBand._meta.get_field("from_kg")
    start: Decimal = model_field.to_python(values.get("from_kg", model_field.get_default()))
    return start


def _assign[M: models.Model](obj: M, values: Mapping[str, Any]) -> M:
    for name, value in values.items():
        setattr(obj, name, value)
    return obj


# --- The standard template


def template_snapshot(template: ShippingTemplate) -> dict[str, Any]:
    return {name: getattr(template, name) for name in TEMPLATE_FIELDS}


def save_template(
    template: ShippingTemplate, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Create or change a template, audited as `shipping.shippingtemplate.created|updated`.

    Its document is checked like a vendor file (shape and every row, with the models'
    validators) before anything is written; raises `InvalidConfig` with the per-row report.
    """
    rows = check_rows(fmt.parse_json(template.document), vendor=None, platform=False)
    if any(row.errors for row in rows):
        raise InvalidConfig(rows)
    template.full_clean()
    return save_audited(
        template,
        template_snapshot,
        key=str(template.public_id),
        ctx=ctx,
        created=TEMPLATE_CREATED,
        updated=TEMPLATE_UPDATED,
        dry_run=dry_run,
    )


def standard_template() -> ShippingTemplate | None:
    """The template the platform proposes to vendors, if staff set one."""
    return ShippingTemplate.objects.filter(is_standard=True).first()


def copy_template(
    template: ShippingTemplate, vendor: Vendor, *, ctx: AuditContext, dry_run: bool = False
) -> ImportResult:
    """Copy `template` into `vendor`'s configuration, which the vendor then customises.

    An import of the template's document (same checks, report, dry run and audit), with the
    source `template:<code>`; the template does not change with the vendor's copy.
    """
    return import_config(
        fmt.parse_json(template.document),
        vendor=vendor,
        ctx=ctx,
        dry_run=dry_run,
        source=f"template:{template.code}",
    )
