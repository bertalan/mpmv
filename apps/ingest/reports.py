"""The ingest daily report (ING.10): gather the day's rows, build the report, mail it to staff.

The builder is pure (`engine.build_daily_report`); this module reads the database and sends the
email through the outbox. Sources are read one at a time, each behind its own guard: a source
whose rows cannot be read is named in the report and the others still go out.
"""

import datetime as dt
import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Count

from apps.core.alerts import alert_address
from apps.core.audit import AuditContext, record
from apps.core.observability import redact_text
from apps.core.services import enqueue_email
from apps.core.tasks import business_tz

from . import engine
from .models import ImportRun, ReviewItem, SourceItem, SupplierSource

logger = logging.getLogger(__name__)

REPORT_SENT = "ingest.daily_report.sent"
EMAIL_TEMPLATE = "ingest.daily_report"


def _window(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """The business day `day` as two UTC instants: 23 or 25 hours long on a DST change day."""
    tz = business_tz()
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    return start.astimezone(dt.UTC), end.astimezone(dt.UTC)


def _error_text(run: ImportRun) -> str:
    """The first log line of a failed run, redacted like any log (secrets, URLs' tokens, emails)."""
    if run.status != "failed":
        return ""
    line = (run.log.splitlines() or [""])[0]
    return redact_text(line, limit=engine.REPORT_ERROR_CHARS)


def _source_rows(
    source: SupplierSource, day: dt.date
) -> tuple[list[engine.ReportRun], list[engine.ReportReview], list[engine.ReportCollision], int]:
    """Runs, review items, SKU collisions and missing items of one source (one vendor)."""
    start, end = _window(day)
    vendor = source.vendor.display_name
    runs = [
        engine.ReportRun(
            source=source.code,
            vendor=vendor,
            kind=run.kind,
            status=run.status,
            dry_run=run.dry_run,
            seen=run.seen,
            created=run.created,
            updated=run.updated,
            unchanged=run.unchanged,
            skipped=run.skipped,
            errors=run.errors,
            error_text=_error_text(run),
        )
        for run in ImportRun.objects.filter(
            source=source, created_at__gte=start, created_at__lt=end
        )
    ]
    reviews = [
        engine.ReportReview(
            source=source.code,
            vendor=vendor,
            kind=review.kind,
            sku=review.item.sku if review.item else "",
            open=review.decision in engine.OPEN_DECISIONS,
            opened_today=start <= review.created_at < end,
            blocking=review.blocking,
        )
        for review in ReviewItem.objects.filter(run__source=source)
        .select_related("item")
        .exclude(created_at__lt=start, decision__in=[engine.ACCEPTED, engine.REJECTED])
    ]
    collisions = (
        [
            engine.ReportCollision(vendor=vendor, sku_key=row["sku_key"], items=row["n"])
            for row in SourceItem.objects.filter(vendor_id=source.vendor_id)
            .exclude(sku_key="")
            .values("sku_key")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
        ]
        if _first_source_of_vendor(source)
        else []
    )
    missing = SourceItem.objects.filter(source=source, missing_since__isnull=False).count()
    return runs, reviews, collisions, missing


def _first_source_of_vendor(source: SupplierSource) -> bool:
    """Collisions are per vendor, so report them once: with the vendor's first active source."""
    first = (
        SupplierSource.objects.filter(vendor_id=source.vendor_id, is_active=True)
        .order_by("pk")
        .values_list("pk", flat=True)
        .first()
    )
    return first == source.pk


def gather_daily_report(day: dt.date) -> engine.DailyReport:
    """The report of the business day `day` over every active source."""
    runs: list[engine.ReportRun] = []
    reviews: list[engine.ReportReview] = []
    collisions: list[engine.ReportCollision] = []
    missing = 0
    unavailable: list[str] = []
    sources = SupplierSource.objects.filter(is_active=True).select_related("vendor").order_by("pk")
    for source in sources:
        try:
            part = _source_rows(source, day)
        except Exception:
            logger.exception("ingest daily report: source %s could not be read", source.code)
            unavailable.append(source.code)
            continue
        runs += part[0]
        reviews += part[1]
        collisions += part[2]
        missing += part[3]
    return engine.build_daily_report(
        day,
        runs=runs,
        reviews=reviews,
        collisions=collisions,
        missing_items=missing,
        unavailable=unavailable,
    )


def send_daily_report(day: dt.date, *, ctx: AuditContext) -> bool:
    """Mail the report of `day` to the staff address, once per day. Returns whether it was sent.

    The recipient is the operations alert address (`OPS_ALERT_EMAIL`, else the platform support
    address); with none configured nothing is sent. The email is plain: no tracking, no images."""
    recipient = alert_address()
    if not recipient:
        logger.warning("ingest daily report: no staff email address is configured")
        return False
    report = gather_daily_report(day)
    context = engine.report_context(report)
    with transaction.atomic():
        changes = enqueue_email(
            recipient=recipient,
            template_key=EMAIL_TEMPLATE,
            language=settings.LANGUAGE_CODE,
            context=context,
            idempotency_key=f"ingest:daily-report:{day.isoformat()}",
        )
        if changes.is_empty:
            return False
        record(
            ctx,
            REPORT_SENT,
            "ingest.dailyreport",
            day.isoformat(),
            None,
            {
                "runs": report.runs_total,
                "failed": report.runs_failed,
                "anomalies": len(report.anomalies),
                "unavailable": len(report.unavailable),
            },
        )
    return True
