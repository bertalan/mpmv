"""The checkout legal step (ORD.17, D-40, counsel variant A): what the buyer reads and accepts
before paying, and what the order keeps.

Reads the customer terms in force (CMS legal page, CMP.5), each seller's terms version in force
(VEN.35) and `current_operator()`; the rules themselves are pure and live in `legal_engine`.
A seller without a terms version in force is shown as "no seller terms published" and does not
block the order: the operator's `require_sales_terms` setting already makes such a seller sell on
request only (VEN.35), so a cart that reaches this step has none unless the setting is off.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.cms import services as cms_services
from apps.core.countries import normalize_country
from apps.core.services import NoPlatformOperator, current_operator
from apps.vendors import terms_services
from apps.vendors.models import SalesTermsVersion, Vendor

from . import legal_engine as engine


@dataclass(frozen=True)
class SellerRecap:
    vendor_id: int
    public_id: str
    name: str
    legal_name: str
    address: str
    vat_number: str
    contact_email: str
    has_terms: bool
    version_id: int | None = None
    version_number: int = 0
    version_date: dt.date | None = None
    content_hash: str = ""
    terms_url: str = ""
    delivery_days: int = 0
    consumer_pays_return: bool = False
    return_cost_info: str = ""
    court: str = ""
    governing_law: str = ""


@dataclass(frozen=True)
class Box2Group:
    label: str
    clauses: Sequence[tuple[str, str]]


@dataclass
class LegalStep:
    business: bool
    platform: cms_services.TermsInForce | None
    operator_name: str
    sellers: list[SellerRecap]
    box2_groups: list[Box2Group] = field(default_factory=list)
    platform_court: str = ""
    platform_url: str = ""
    privacy_url: str = ""

    @property
    def box2_required(self) -> bool:
        return bool(self.box2_groups)

    def box2_text(self) -> list[str]:
        return [
            f"{group.label}: {article} - {title}"
            for group in self.box2_groups
            for article, title in group.clauses
        ]


def _address(vendor: Vendor) -> str:
    street = " ".join(p for p in (vendor.registered_street, vendor.registered_street_number) if p)
    town = " ".join(p for p in (vendor.registered_postal_code, vendor.registered_city) if p)
    parts = [street, town, str(vendor.registered_country or vendor.country)]
    return ", ".join(p for p in parts if p)


def _established(vendor: Vendor) -> str:
    return str(vendor.registered_country or vendor.country or "")


def _seller(vendor: Vendor, *, business: bool) -> tuple[SellerRecap, engine.Party | None]:
    base: dict[str, Any] = {
        "vendor_id": vendor.pk,
        "public_id": str(vendor.public_id),
        "name": vendor.display_name,
        "legal_name": vendor.legal_name or vendor.display_name,
        "address": _address(vendor),
        "vat_number": vendor.vat_number,
        "contact_email": vendor.contact_email,
    }
    version = terms_services.version_in_force(vendor)
    if version is None:
        return SellerRecap(has_terms=False, **base), None
    facts = terms_services.recap(version, buyer_is_business=business)
    court = facts.b2b_court or "" if business else _("the court of your own place of residence")
    recap = SellerRecap(
        has_terms=True,
        version_id=version.pk,
        version_number=version.number,
        version_date=version.effective_from,
        content_hash=version.content_hash,
        terms_url=terms_services.terms_url(version),
        delivery_days=facts.delivery_days,
        consumer_pays_return=facts.consumer_pays_return,
        return_cost_info=facts.return_cost_info,
        court=court,
        governing_law=facts.governing_law,
        **base,
    )
    party = engine.Party(
        "seller",
        vendor.display_name,
        _established(vendor),
        facts.governing_law,
        tuple((str(c["article"]), str(c["title"])) for c in version.clauses),
    )
    return recap, party


def build_step(vendors: Sequence[Vendor], *, language: str, business: bool) -> LegalStep:
    """The recap per seller, the platform terms, and box 2 when it applies (never for a
    consumer; for a business only with an Italian operator or seller)."""
    try:
        operator = current_operator()
        operator_country = normalize_country(str(operator.country))
        operator_name = operator.legal_name
    except NoPlatformOperator:
        operator_country, operator_name = "", ""
    platform = cms_services.customer_terms_in_force(language)
    sellers: list[SellerRecap] = []
    parties: list[engine.Party] = []
    if platform is not None:
        parties.append(
            engine.Party("platform", operator_name, operator_country, "", platform.clauses)
        )
    for vendor in vendors:
        recap, party = _seller(vendor, business=business)
        sellers.append(recap)
        if party is not None:
            parties.append(party)
    shown = engine.box2_parties(parties, business=business, operator_country=operator_country)
    return LegalStep(
        platform_url=cms_services.legal_page_url("terms", language),
        privacy_url=cms_services.legal_page_url("privacy", language),
        business=business,
        platform=platform,
        operator_name=operator_name,
        sellers=sellers,
        box2_groups=[Box2Group(p.label, p.clauses) for p in shown],
        platform_court=(
            _("the court of the seat of %(operator)s") % {"operator": operator_name}
            if business
            else _("the court of your own place of residence")
        ),
    )


def check(step: LegalStep, post: Mapping[str, Any]) -> list[str]:
    """What is missing from the submitted boxes, in words. A box counts only when sent as `on`."""
    codes = engine.acceptance_errors(
        box1=_ticked(post, engine.BOX1),
        box2=_ticked(post, engine.BOX2),
        box2_shown=step.box2_required,
    )
    words = {
        "box1": _("Tick the box to accept the terms before you place the order."),
        "box2": _("Tick the second box to approve the listed clauses before you place the order."),
    }
    return [words[code] for code in codes]


def _ticked(post: Mapping[str, Any], name: str) -> bool:
    return str(post.get(name, "")).lower() in {"on", "true", "1", "yes"}


def acceptance(step: LegalStep, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """What the order stores: the versions accepted and the time. Call only after `check`."""
    stamp = (now or timezone.now()).isoformat()
    platform = step.platform
    return {
        "accepted_at": stamp,
        "box1": True,
        "business": step.business,
        "platform": None
        if platform is None
        else {
            "version_id": platform.version_id,
            "number": platform.number,
            "language": platform.language,
            "sha256": platform.sha256,
        },
        "sellers": [
            {
                "vendor": s.public_id,
                "version_id": s.version_id,
                "number": s.version_number,
                "content_hash": s.content_hash,
            }
            for s in step.sellers
        ],
        "box2": {
            "shown": step.box2_required,
            "accepted": step.box2_required,
            "text": step.box2_text(),
        },
    }


def terms_documents(order_terms: Mapping[str, Any]) -> dict[str, Any]:
    """The texts of the versions an order accepted, for the confirmation email (art. 51(7): a
    durable medium, the text itself and not only a link). Empty parts are left out."""
    documents: dict[str, Any] = {"platform": None, "sellers": []}
    platform = order_terms.get("platform")
    if isinstance(platform, Mapping):
        found = cms_services.legal_version_terms(int(platform["version_id"]))
        if found is not None:
            documents["platform"] = {
                "title": found.title,
                "number": found.number,
                "text": found.text,
            }
    for entry in order_terms.get("sellers") or []:
        version_id = entry.get("version_id")
        if not version_id:
            continue
        version = SalesTermsVersion.objects.filter(pk=version_id).select_related("vendor").first()
        if version is not None:
            documents["sellers"].append(
                {
                    "name": version.vendor.display_name,
                    "number": version.number,
                    "text": version.body,
                }
            )
    return documents
