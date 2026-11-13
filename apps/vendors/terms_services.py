"""A seller's sales terms: publish a version, read the version in force (VEN.35, D-40).

A publish never edits a stored version: it adds the next one and moves the pointer
(`SalesTermsInForce`). The version in force is readable by anyone before any order through a
signed, expiring, multi-use link, and every read is logged; a superseded version is not public
(it stays readable through an order snapshot once ORD.17 stores one).

Deferred to the rows that own them: the VEN.32 private document store (PDF of a version), the
ORD.17 checkout snapshot, the VEN.35 settings per shipping class (SHP.4).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.countries import InvalidCountry, normalize_country
from apps.core.models import AuditLog
from apps.core.services import run_mutation

from . import terms_engine as engine
from .models import SalesTermsInForce, SalesTermsVersion, Vendor
from .services import PROFILE_LOCKED, has_vendor_permission

PUBLISHED: Final = "vendor.sales_terms.published"
READ: Final = "vendor.sales_terms.read"
LINK_SALT: Final = "vendors:sales-terms:link"
LINK_MAX_AGE: Final = 24 * 3600  # seconds; the link is multi-use within this time


@dataclass(frozen=True)
class Recap:
    """What the checkout recap of one seller shows for one buyer kind (ORD.17 consumes it)."""

    version_id: int
    number: int
    delivery_days: int
    governing_law: str
    #: The court for a business buyer; None for a consumer, whose court is never moved.
    b2b_court: str | None
    consumer_court_rule: str
    consumer_pays_return: bool
    return_cost_info: str


def version_in_force(vendor: Vendor) -> SalesTermsVersion | None:
    pointer = SalesTermsInForce.objects.select_related("version").filter(vendor=vendor).first()
    return None if pointer is None else pointer.version


def can_sell_by_purchase(vendor: Vendor) -> bool:
    """A vendor sells by direct purchase only with a terms version in force (else on request)."""
    return version_in_force(vendor) is not None


def recap(version: SalesTermsVersion, *, buyer_is_business: bool) -> Recap:
    pays = engine.consumer_bears_return_cost(version.return_cost_bearer, version.return_cost_info)
    return Recap(
        version_id=version.pk,
        number=version.number,
        delivery_days=version.delivery_days,
        governing_law=str(version.governing_law),
        b2b_court=engine.competent_court(
            version.b2b_court, version.vendor.registered_city, buyer_is_business=buyer_is_business
        ),
        consumer_court_rule=engine.CONSUMER_COURT_RULE,
        consumer_pays_return=pays and not buyer_is_business,
        return_cost_info=version.return_cost_info if pays else "",
    )


def publish_sales_terms(
    vendor: Vendor,
    values: Mapping[str, Any],
    *,
    actor: User,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """The vendor owner publishes the next version of its sales terms and puts it in force.

    Owners only (`profile` in the role matrix); a suspended or closed vendor cannot. A version
    without the clause list, the governing law, the delivery time and the text is refused
    (`ValidationError` with one message per field). The old version is untouched.
    """
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner publishes the sales terms.")
    unknown = sorted(set(values) - set(engine.FIELDS))
    if unknown:
        raise ValidationError(f"Not sales terms fields: {', '.join(unknown)}.")
    errors = engine.terms_errors(values)
    if errors:
        raise ValidationError({name: reason for name, reason in errors.items()})
    try:
        law = normalize_country(str(values["governing_law"]))
    except InvalidCountry as error:
        raise ValidationError({"governing_law": str(error)}) from error
    changes = ChangeSet()
    with transaction.atomic():
        locked = Vendor.objects.select_for_update().get(pk=vendor.pk)
        if locked.status in PROFILE_LOCKED:
            raise PermissionDenied("A suspended or closed vendor cannot change its terms.")
        previous = version_in_force(vendor)
        last = (
            SalesTermsVersion.objects.filter(vendor=vendor)
            .order_by("-number")
            .values_list("number", flat=True)
            .first()
        )
        version = SalesTermsVersion(
            vendor=vendor,
            number=(last or 0) + 1,
            effective_from=timezone.localdate(),
            body=str(values["body"]).strip(),
            clauses=[
                {"article": str(c["article"]).strip(), "title": str(c["title"]).strip()}
                for c in values["clauses"]
            ],
            governing_law=law,
            b2b_court=str(values.get("b2b_court") or "").strip(),
            delivery_days=int(values["delivery_days"]),
            return_cost_bearer=str(values.get("return_cost_bearer") or ""),
            return_cost_info=str(values.get("return_cost_info") or "").strip(),
        )
        version.content_hash = engine.content_hash(
            {**values, "body": version.body, "clauses": version.clauses}
        )
        changes.create(
            "vendors.salestermsversion",
            f"{vendor.public_id}:{version.number}",
            version.snapshot(),
            supersedes=None if previous is None else previous.snapshot(),
        )

        def apply() -> None:
            version.save()
            SalesTermsInForce.objects.update_or_create(vendor=vendor, defaults={"version": version})

        run_mutation(changes, apply, ctx=ctx, action=PUBLISHED, dry_run=dry_run)
    return changes


def terms_url(version: SalesTermsVersion) -> str:
    """The signed, expiring, multi-use link to a version (issued by the checkout, ORD.17)."""
    token = signing.dumps(str(version.public_id), salt=LINK_SALT)
    return reverse("vendor_terms:read", args=[token])


def read_terms(token: str, *, ctx: AuditContext) -> SalesTermsVersion | None:
    """The version a link names, if it is valid and still in force; every attempt is logged.

    A tampered or expired token, an unknown version and a superseded one all answer None.
    The log holds the result and the version, never the reader's name or address.
    """
    version: SalesTermsVersion | None = None
    try:
        public_id = signing.loads(token, salt=LINK_SALT, max_age=LINK_MAX_AGE)
        candidate = (
            SalesTermsVersion.objects.select_related("vendor").filter(public_id=public_id).first()
        )
    except (signing.BadSignature, ValueError, ValidationError):
        candidate = None
    if candidate is not None:
        pointer = SalesTermsInForce.objects.filter(vendor=candidate.vendor).first()
        if pointer is not None and pointer.version_id == candidate.pk:
            version = candidate
    if version is None:
        record(ctx, READ, "vendors.salestermsversion", "", None, {"result": "denied"})
    else:
        record(
            ctx,
            READ,
            "vendors.salestermsversion",
            str(version.public_id),
            None,
            {
                "result": "ok",
                "vendor": str(version.vendor.public_id),
                "content_hash": version.content_hash,
            },
        )
    return version


def anonymous_reader(ip: str | None) -> AuditContext:
    return AuditContext(actor_label="anonymous", source=AuditLog.Source.SYSTEM, ip=ip)
