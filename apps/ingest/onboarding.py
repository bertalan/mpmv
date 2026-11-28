"""Onboarding a vendor from a source file, without code (ING.13, spec 08 "Onboarding").

I/O services behind the Manager wizard and the checklist: pick an adapter, read a sample file
as a dry run, record what the sample showed and say what is still missing before the source can
be activated. The sample is parsed in memory and dropped: only the counts, the header and a short
fingerprint of the mapping it was read with are kept, in `SupplierSource.config["sample"]`, so
no supplier file and no row content is stored. Every write goes through `services.save_source`
(audited, dry-run aware).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.vendors.models import Vendor

from . import engine, services
from .adapters import base, csv_generic, publisher_feed, xlsx_pricelist, yaml_light
from .adapters.table import COLUMN_KIND
from .models import MappingTable, SupplierSource

# adapter code -> (label for the wizard, adapter class). The four file adapters of ING.9.
ADAPTERS: Final[Mapping[str, tuple[str, type]]] = {
    csv_generic.CODE: ("CSV file", csv_generic.CsvAdapter),
    xlsx_pricelist.CODE: ("XLSX price list", xlsx_pricelist.XlsxAdapter),
    publisher_feed.CODE: ("JSON feed", publisher_feed.FeedAdapter),
    yaml_light.CODE: ("YAML file", yaml_light.YamlAdapter),
}
DECIMALS: Final = (",", ".")
MAX_PREVIEW_ROWS: Final = 10
MAX_LISTED_ERRORS: Final = 50
MAX_HEADERS: Final = base.DEFAULT_MAX_COLUMNS
MAX_HEADER_CHARS: Final = 100  # a column name kept in the source config


def column_entries(vendor: Vendor) -> list[engine.MappingEntry]:
    """The vendor's own header mappings (`MappingTable` kind `other`) as engine entries."""
    rows = MappingTable.objects.filter(vendor=vendor, kind=COLUMN_KIND).order_by("key_norm")
    return [
        engine.MappingEntry(
            vendor_id=row.vendor_id,
            kind=row.kind,
            key=row.key,
            aliases=tuple(str(a) for a in row.aliases),
            target_type="",
            target_id=None,
            target_value=row.target_value,
            ref=row.pk,
        )
        for row in rows
    ]


def mapping_fingerprint(entries: list[engine.MappingEntry]) -> str:
    return engine.payload_hash({e.key: e.target_value for e in entries})


def source_options(source: SupplierSource) -> dict[str, Any]:
    options = source.config.get("options")
    return dict(options) if isinstance(options, dict) else {}


def build_adapter(source: SupplierSource) -> Any:
    """The adapter of `source` with the vendor's mappings and the source's options."""
    spec = ADAPTERS.get(source.adapter_code)
    if spec is None:
        raise base.AdapterError("This source names an adapter that does not exist.")
    options = {k: v for k, v in source_options(source).items() if k == "decimal" and v in DECIMALS}
    return spec[1](column_entries(source.vendor), vendor_id=source.vendor_id, **options)


@dataclass
class SampleReport:
    """What a dry run of one sample file showed. Holds no file content beyond the preview."""

    failure: str = ""  # the whole file was refused
    headers: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    rows: int = 0
    preview: list[dict[str, Any]] = field(default_factory=list)
    errors: list[base.RowError] = field(default_factory=list)
    error_count: int = 0
    mapping: str = ""

    @property
    def clean(self) -> bool:
        return not self.failure and self.rows > 0 and self.error_count == 0


def run_sample(source: SupplierSource, data: bytes) -> SampleReport:
    """Dry-run the source's adapter on `data`. Reads only: nothing is written or stored."""
    report = SampleReport(mapping=mapping_fingerprint(column_entries(source.vendor)))
    adapter = build_adapter(source)
    try:
        result = adapter.parse(data)
    except base.AdapterError as exc:
        report.failure = str(exc)
        report.headers = adapter.table.last_header[:MAX_HEADERS]
        return report
    report.headers = adapter.table.last_header[:MAX_HEADERS]
    report.unmapped = result.unmapped_columns[:MAX_HEADERS]
    report.rows = len(result.rows)
    report.preview = [r.product.as_json() for r in result.rows[:MAX_PREVIEW_ROWS]]
    report.error_count = len(result.errors)
    report.errors = result.errors[:MAX_LISTED_ERRORS]
    return report


def create_source(
    vendor: Vendor,
    *,
    code: str,
    adapter_code: str,
    decimal: str = "",
    ctx: AuditContext,
    dry_run: bool = False,
) -> tuple[SupplierSource, ChangeSet]:
    """A new, inactive source. Active and write-enabled stay off until the checklist is met."""
    if adapter_code not in ADAPTERS:
        raise ValidationError({"adapter_code": "Choose one of the listed adapters."})
    if decimal and decimal not in DECIMALS:
        raise ValidationError({"decimal": "The decimal separator is a comma or a point."})
    if SupplierSource.objects.filter(vendor=vendor, code=code).exists():
        raise ValidationError({"code": "This vendor already has a source with this name."})
    source = SupplierSource(
        vendor=vendor,
        code=code,
        adapter_code=adapter_code,
        config={"options": {"decimal": decimal} if decimal else {}},
        is_active=False,
        write_enabled=False,
    )
    return source, services.save_source(source, ctx=ctx, dry_run=dry_run)


def record_sample(
    source: SupplierSource, report: SampleReport, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Keep the counts of a sample read (no rows, no file) so the checklist can use them."""
    source.config = {
        **source.config,
        "sample": {
            "ok": report.clean,
            "rows": report.rows,
            "errors": report.error_count,
            "mapping": report.mapping,
            "headers": [h[:MAX_HEADER_CHARS] for h in report.headers],
            "checked_at": timezone.now().isoformat(timespec="seconds"),
        },
    }
    return services.save_source(source, ctx=ctx, dry_run=dry_run)


@dataclass(frozen=True)
class CheckItem:
    key: str
    ok: bool


def checklist(source: SupplierSource) -> list[CheckItem]:
    """What must hold before activation, in the order the wizard fixes it."""
    entries = column_entries(source.vendor)
    mapped = {e.target_value for e in entries}
    sample = source.config.get("sample")
    sample = sample if isinstance(sample, dict) else {}
    return [
        CheckItem("vendor_active", source.vendor.status == Vendor.Status.ACTIVE),
        CheckItem("adapter_known", source.adapter_code in ADAPTERS),
        CheckItem("mapping_complete", all(f in mapped for f in base.REQUIRED_FIELDS)),
        CheckItem(
            "sample_clean",
            bool(sample.get("ok")) and sample.get("mapping") == mapping_fingerprint(entries),
        ),
        CheckItem("source_active", source.is_active),
    ]


def save_column_mappings(
    vendor: Vendor, columns: Mapping[str, str], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Map file headers to generic fields (`MappingTable` kind `other`): add or update one row per
    header, all or none, audited by `services.save_mapping`. A known field is required."""
    if len(columns) > MAX_HEADERS:
        raise ValidationError("Too many columns.")
    for header, name in columns.items():
        if name not in base.FIELDS:
            raise ValidationError({"field": f"{name[:40]!r} is not a field a column can map to."})
        if not engine.mapping_key(header):
            raise ValidationError({"header": "A column name cannot be empty."})
    changes = ChangeSet()
    with transaction.atomic():
        for header, name in columns.items():
            entry = MappingTable.objects.filter(
                vendor=vendor, kind=COLUMN_KIND, key_norm=engine.mapping_key(header)
            ).first() or MappingTable(vendor=vendor, kind=COLUMN_KIND, key=header[:200])
            entry.target_value = name
            changes.changes.extend(services.save_mapping(entry, ctx=ctx, dry_run=dry_run).changes)
    return changes


def activate_source(
    source: SupplierSource, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Turn the source on once everything but the activation itself is in place. It does not
    enable writing: runs stay dry until that flag is set on purpose."""
    missing = [i.key for i in checklist(source) if not i.ok and i.key != "source_active"]
    if missing:
        raise ValidationError("The checklist is not met: " + ", ".join(missing) + ".")
    source.is_active = True
    return services.save_source(source, ctx=ctx, dry_run=dry_run)
