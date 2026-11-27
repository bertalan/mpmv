"""Tenant access of every endpoint, API operation and vendor-owned model (CORE.3).

`tests.integration.test_tenant_isolation` generates one negative cross-tenant test per entry and
fails when an endpoint, an API operation or a vendor-owned model is missing here. When you add:

- a vendor-owned model: give it the scoped manager (`apps.vendors.scoping`) and add an object
  maker to `OWNED_OBJECTS`;
- a page or API operation that shows or changes one vendor's object: declare it `VendorScoped`
  with the maker of that object;
- a list, export or search of vendor-owned objects (page or API): `VendorScopedList` with the
  maker of the listed objects and, when they do not show their public id, `identify`;
- a page or API operation that shows nothing of a vendor: `NoVendorData` with a reason. Never
  declare a list of vendor data `NoVendorData`: it is only checked against Vendor B's seeded
  objects, while `VendorScopedList` also proves that the caller's own objects are listed.

API v1 operations are called with an API key bound to Vendor A that holds the operation's scope
(CORE.13), so every operation on vendor data must scope it with `for_actor(api_client_of(request))`
or `get_for_actor_or_404`.

The Django admin is classified as a whole: staff only, with the object pages of vendor-owned
models tested on Vendor B's objects.
"""

import secrets
from decimal import Decimal

from django.contrib import admin
from django.db import models
from django.test import Client

from apps.api.models import ApiClient, ApiRateCounter
from apps.api.tests.factories import ApiClientFactory, ApiRateCounterFactory
from apps.cart.models import CartLine
from apps.cart.tests.factories import CartLineFactory
from apps.catalog.models import (
    BrandBatch,
    PriceHistory,
    Product,
    ProductAttribute,
    ProductImage,
    ProductImageTranslation,
    ProductTranslation,
    SkuAlias,
    SlugHistory,
    Variant,
    VariantOption,
    VariantTranslation,
)
from apps.catalog.tests.factories import (
    BrandBatchFactory,
    PriceHistoryFactory,
    ProductAttributeFactory,
    ProductFactory,
    ProductImageFactory,
    ProductImageTranslationFactory,
    ProductTranslationFactory,
    SlugHistoryFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    VariantFactory,
    VariantOptionFactory,
    VariantTranslationFactory,
)
from apps.cms.models import VendorLandingPage
from apps.cms.tests.factories import VendorLandingPageFactory
from apps.compliance.models import (
    GpsrInfo,
    GpsrSafetyText,
    ProductRestriction,
    SafetyCase,
    StatementOfReasons,
    TerminationNotice,
    TransparencySubmission,
    VendorGpsrDefaults,
    WithdrawalStatement,
)
from apps.compliance.tests.factories import (
    GpsrInfoFactory,
    GpsrSafetyTextFactory,
    ProductRestrictionFactory,
    SafetyCaseFactory,
    StatementOfReasonsFactory,
    TerminationNoticeFactory,
    TransparencySubmissionFactory,
    VendorGpsrDefaultsFactory,
    WithdrawalStatementFactory,
)
from apps.core.models import TranslationStatus
from apps.core.tests.factories import utc
from apps.events.models import EventPage, VendorEventPolicy
from apps.events.tests.factories import EventPageFactory, VendorEventPolicyFactory
from apps.fitment.models import FitmentNodeProposal, ProductFitment, ProductFitmentState
from apps.fitment.tests.factories import (
    FitmentCategoryFactory,
    FitmentNodeProposalFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.ingest.models import (
    ImportRun,
    ItemChange,
    MappingTable,
    ProductDraft,
    ReviewItem,
    SourceItem,
    SourceMissing,
    SourceObservation,
    SourceState,
    SourceStateVersion,
    SourceVariation,
    SupplierSource,
)
from apps.ingest.tests.factories import (
    ImportRunFactory,
    ItemChangeFactory,
    MappingTableFactory,
    ProductDraftFactory,
    ReviewItemFactory,
    SourceItemFactory,
    SourceMissingFactory,
    SourceObservationFactory,
    SourceStateFactory,
    SourceStateVersionFactory,
    SourceVariationFactory,
    SupplierSourceFactory,
)
from apps.ledger.models import LedgerEntry, Refund
from apps.ledger.tests.factories import LedgerEntryFactory, RefundFactory
from apps.orders.models import (
    OrderAmendment,
    OrderEvent,
    OrderLine,
    StockReservation,
    VendorOrder,
    VendorOrderVersion,
)
from apps.orders.tests.factories import (
    OrderAmendmentFactory,
    OrderEventFactory,
    OrderLineFactory,
    StockReservationFactory,
    VendorOrderFactory,
    paid_vendor_order,
    version_one,
)
from apps.payments.models import ConnectedAccount
from apps.payments.tests.factories import ConnectedAccountFactory
from apps.shipping.models import (
    PickupOption,
    PickupOptionTranslation,
    ShippingMethod,
    ShippingMethodTranslation,
    ShippingZone,
    SpecialAreaTreatment,
    WeightBand,
)
from apps.shipping.tests.factories import (
    PickupOptionFactory,
    PickupOptionTranslationFactory,
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    SpecialAreaTreatmentFactory,
    WeightBandFactory,
)
from apps.tax.models import TaxRegimeChangeRequest, VendorTaxProfile, VendorTaxProfileTranslation
from apps.tax.tests.factories import (
    TaxRegimeChangeRequestFactory,
    VendorTaxProfileFactory,
    VendorTaxProfileTranslationFactory,
)
from apps.trust.models import VendorTrustSnapshot
from apps.trust.tests.factories import VendorTrustSnapshotFactory
from apps.vendors.models import (
    ContractAcceptance,
    CrossVendorConsent,
    ReturnPolicyVersion,
    SalesTermsInForce,
    SalesTermsVersion,
    SelfCertification,
    Vendor,
    VendorClaim,
    VendorClosure,
    VendorFeatureFlag,
    VendorMember,
    VendorTranslation,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    CrossVendorConsentFactory,
    ReturnPolicyVersionFactory,
    SalesTermsInForceFactory,
    SalesTermsVersionFactory,
    SelfCertificationFactory,
    VendorClaimFactory,
    VendorClosureFactory,
    VendorFeatureFlagFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)
from tests.tenancy import (
    Access,
    CrossTenant,
    Endpoint,
    NoVendorData,
    ObjectMaker,
    StaffOnly,
    VendorScoped,
    VendorScopedList,
    by_public_id,
    member_session,
    vendor_api_key,
)

# --- Vendor-owned models: how to make one object owned by a given vendor


def renamed_product_alias(vendor: Vendor) -> SkuAlias:
    """The alias a SKU rename leaves behind (written by the database, CAT.13)."""
    product = ProductFactory.create(vendor=vendor)
    product.sku = f"{product.sku}-RENAMED"
    product.save()
    return SkuAlias.objects.get(product=product)


OWNED_OBJECTS: dict[type[models.Model], ObjectMaker] = {
    Vendor: lambda vendor: vendor,
    ApiClient: lambda vendor: ApiClientFactory.create(vendor=vendor),
    ApiRateCounter: lambda vendor: ApiRateCounterFactory.create(client__vendor=vendor),
    VendorMember: lambda vendor: VendorMemberFactory.create(
        vendor=vendor, role=VendorMember.Role.STAFF
    ),
    VendorTranslation: lambda vendor: VendorTranslationFactory.create(vendor=vendor, language="it"),
    ContractAcceptance: lambda vendor: ContractAcceptanceFactory.create(
        vendor=vendor, certified=False
    ),
    SelfCertification: lambda vendor: SelfCertificationFactory.create(vendor=vendor),
    # TRU.1: a vendor's computed trust result; the Manager shows a vendor its own (TRU.2).
    VendorTrustSnapshot: lambda vendor: VendorTrustSnapshotFactory.create(vendor=vendor),
    # VEN.35: a published version of the seller's sales terms and the pointer to the one in force.
    ReturnPolicyVersion: lambda vendor: ReturnPolicyVersionFactory.create(vendor=vendor),
    SalesTermsVersion: lambda vendor: SalesTermsVersionFactory.create(vendor=vendor),
    SalesTermsInForce: lambda vendor: SalesTermsInForceFactory.create(version__vendor=vendor),
    VendorFeatureFlag: lambda vendor: VendorFeatureFlagFactory.create(vendor=vendor),
    CrossVendorConsent: lambda vendor: CrossVendorConsentFactory.create(vendor=vendor),
    VendorClaim: lambda vendor: VendorClaimFactory.create(vendor=vendor),
    VendorClosure: lambda vendor: VendorClosureFactory.create(vendor=vendor),
    Product: lambda vendor: ProductFactory.create(vendor=vendor),
    ProductTranslation: lambda vendor: ProductTranslationFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    FitmentNodeProposal: lambda vendor: FitmentNodeProposalFactory.create(vendor=vendor),
    ProductFitmentState: lambda vendor: ProductFitmentStateFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    ProductFitment: lambda vendor: ProductFitmentFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    SlugHistory: lambda vendor: SlugHistoryFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    PriceHistory: lambda vendor: PriceHistoryFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    Variant: lambda vendor: VariantFactory.create(
        product=ProductFactory.create(vendor=vendor, kind=Product.Kind.VARIABLE)
    ),
    VariantTranslation: lambda vendor: VariantTranslationFactory.create(
        variant=VariantFactory.create(
            product=ProductFactory.create(vendor=vendor, kind=Product.Kind.VARIABLE)
        )
    ),
    BrandBatch: lambda vendor: BrandBatchFactory.create(vendor=vendor),
    SkuAlias: lambda vendor: renamed_product_alias(vendor),
    ProductImage: lambda vendor: ProductImageFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    ProductImageTranslation: lambda vendor: ProductImageTranslationFactory.create(
        product_image__product=ProductFactory.create(vendor=vendor)
    ),
    ProductAttribute: lambda vendor: ProductAttributeFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    VariantOption: lambda vendor: VariantOptionFactory.create(
        variant=VariantFactory.create(
            product=ProductFactory.create(vendor=vendor, kind=Product.Kind.VARIABLE)
        )
    ),
    GpsrInfo: lambda vendor: GpsrInfoFactory.create(product=ProductFactory.create(vendor=vendor)),
    VendorGpsrDefaults: lambda vendor: VendorGpsrDefaultsFactory.create(vendor=vendor),
    SupplierSource: lambda vendor: SupplierSourceFactory.create(vendor=vendor),
    SourceItem: lambda vendor: SourceItemFactory.create(source__vendor=vendor),
    SourceObservation: lambda vendor: SourceObservationFactory.create(item__source__vendor=vendor),
    ProductDraft: lambda vendor: ProductDraftFactory.create(item__source__vendor=vendor),
    SourceState: lambda vendor: SourceStateFactory.create(item__source__vendor=vendor),
    SourceStateVersion: lambda vendor: SourceStateVersionFactory.create(
        item__source__vendor=vendor
    ),
    SourceVariation: lambda vendor: SourceVariationFactory.create(item__source__vendor=vendor),
    SourceMissing: lambda vendor: SourceMissingFactory.create(item__source__vendor=vendor),
    ItemChange: lambda vendor: ItemChangeFactory.create(item__source__vendor=vendor),
    ImportRun: lambda vendor: ImportRunFactory.create(source__vendor=vendor),
    ReviewItem: lambda vendor: ReviewItemFactory.create(run__source__vendor=vendor),
    MappingTable: lambda vendor: MappingTableFactory.create(vendor=vendor),
    GpsrSafetyText: lambda vendor: GpsrSafetyTextFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    SafetyCase: lambda vendor: SafetyCaseFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    StatementOfReasons: lambda vendor: StatementOfReasonsFactory.create(vendor=vendor),
    ProductRestriction: lambda vendor: ProductRestrictionFactory.create(
        product=ProductFactory.create(vendor=vendor),
        statement=StatementOfReasonsFactory.create(vendor=vendor),
    ),
    # An applied notice: a vendor has at most one pending one, and the maker may run twice.
    TerminationNotice: lambda vendor: TerminationNoticeFactory.create(
        vendor=vendor, status=TerminationNotice.Status.APPLIED
    ),
    TransparencySubmission: lambda vendor: TransparencySubmissionFactory.create(
        statement=StatementOfReasonsFactory.create(vendor=vendor)
    ),
    WithdrawalStatement: lambda vendor: WithdrawalStatementFactory.create(
        vendor_order=VendorOrderFactory.create(vendor=vendor)
    ),
    ShippingZone: lambda vendor: ShippingZoneFactory.create(vendor=vendor),  # a vendor zone
    ShippingMethod: lambda vendor: ShippingMethodFactory.create(vendor=vendor),
    ShippingMethodTranslation: lambda vendor: ShippingMethodTranslationFactory.create(
        method=ShippingMethodFactory.create(vendor=vendor)
    ),
    WeightBand: lambda vendor: WeightBandFactory.create(
        method=ShippingMethodFactory.create(vendor=vendor)
    ),
    SpecialAreaTreatment: lambda vendor: SpecialAreaTreatmentFactory.create(
        method=ShippingMethodFactory.create(vendor=vendor)
    ),
    PickupOption: lambda vendor: PickupOptionFactory.create(vendor=vendor),
    PickupOptionTranslation: lambda vendor: PickupOptionTranslationFactory.create(
        pickup_option=PickupOptionFactory.create(vendor=vendor)
    ),
    VendorTaxProfile: lambda vendor: VendorTaxProfileFactory.create(vendor=vendor),
    VendorTaxProfileTranslation: lambda vendor: VendorTaxProfileTranslationFactory.create(
        # An earlier, closed profile: the open-ended one may already exist for this vendor.
        profile=VendorTaxProfileFactory.create(
            vendor=vendor, valid_from=utc(2025), valid_to=utc(2026)
        )
    ),
    CartLine: lambda vendor: CartLineFactory.create(product=ProductFactory.create(vendor=vendor)),
    # ORD.2: a sub-order of its own order, and a line of such a sub-order.
    VendorOrder: lambda vendor: VendorOrderFactory.create(vendor=vendor),
    LedgerEntry: lambda vendor: LedgerEntryFactory.create(vendor=vendor),
    # PAY.4: a refund mark of one of the vendor's own orders.
    Refund: lambda vendor: RefundFactory.create(vendor=vendor),
    OrderLine: lambda vendor: OrderLineFactory.create(vendor_order__vendor=vendor),
    # ORD.20: v1 of one of the vendor's orders, and a draft amendment on such an order.
    VendorOrderVersion: lambda vendor: version_one(vendor),
    OrderAmendment: lambda vendor: OrderAmendmentFactory.create(from_version=version_one(vendor)),
    OrderEvent: lambda vendor: OrderEventFactory.create(vendor_order=paid_vendor_order(vendor)),
    StockReservation: lambda vendor: StockReservationFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    # VEN.6: a vendor's connected payout account.
    ConnectedAccount: lambda vendor: ConnectedAccountFactory.create(vendor=vendor),
    # EVT.1: a draft event page under the primary language's events index.
    EventPage: lambda vendor: EventPageFactory.create(vendor=vendor),
    VendorEventPolicy: lambda vendor: VendorEventPolicyFactory.create(vendor=vendor),
    # CMS.4: a vendor's story page under the primary language's home.
    VendorLandingPage: lambda vendor: VendorLandingPageFactory.create(vendor=vendor),
    TaxRegimeChangeRequest: lambda vendor: TaxRegimeChangeRequestFactory.create(
        # A reviewed request: the vendor may already have its one pending request.
        vendor=vendor,
        rejected=True,
    ),
}

# Models with a ForeignKey chain to Vendor that are deliberately not vendor-scoped, by label,
# with the reason.
NOT_VENDOR_OWNED: dict[str, str] = {
    "orders.StaffDraftLine": (
        "An item of a cart that platform staff prepared from a past order (ORD.32): the product is "
        "what the customer may buy again, not an owner; only Support and the platform owner see "
        "drafts, and no vendor endpoint lists them."
    ),
    "vendors.CommissionRule": (
        "Platform pricing configuration managed by staff only (VEN.2): the optional vendor is "
        "the rule's target, not an owner; no vendor endpoint lists or edits commission rules."
    ),
    "catalog.Brand": (
        "Shared catalog entity (CAT.20): approved brands are platform-wide and public; "
        "`proposed_by` and `batch` only say who proposed a pending brand. Another vendor's "
        "pending brand is hidden by `brands.brand_choices` and `Product.clean`, and the public "
        "brand page serves approved brands only; vendors never edit brands."
    ),
    "catalog.BrandTranslation": (
        "Storefront texts of a shared brand (CAT.20); reaches a vendor only through the brand "
        "that vendor proposed, and is edited by staff only."
    ),
    "catalog.Redirect": (
        "Platform SEO rule managed by staff only (CAT.6): its optional target product is where a "
        "legacy URL lands, not an owner; no vendor endpoint lists or edits redirects."
    ),
}


# --- Web endpoints (URL name, or route when unnamed)

OWN_ACCOUNT = "The signed-in user's own second factors (CORE.4); no vendor data."
PUBLIC_LISTING = (
    "Public catalog listing (CAT.8): only published products of active vendors, the same for "
    "every visitor; a non-active vendor's data never shows."
)
SAMPLE_TAXONOMY = "0"  # the sample path's first segment (`_sample_path` fills every param "0")


def member_with_landing_taxonomy(client: Client, tenants: CrossTenant) -> dict[str, str]:
    """`member_session`, plus a taxonomy with term pages whose slug is the sample path's first
    segment: a term route only matches the slug of such a taxonomy."""
    taxonomy = TaxonomyFactory.create(has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, slug=SAMPLE_TAXONOMY, status=TranslationStatus.PUBLISHED
    )
    return member_session(client, tenants)


def manager_vendor(vendor: Vendor) -> Vendor:
    """The Manager's shop pages show the selected vendor itself (CORE.17)."""
    return vendor


def ledger_entry_with_ref(vendor: Vendor) -> LedgerEntry:
    """A sale with a unique provider reference, the field the VEN.9 pages show (CORE.3)."""
    return LedgerEntryFactory.create(vendor=vendor, external_ref=f"tr_{secrets.token_hex(8)}")


def payout_entry_with_ref(vendor: Vendor) -> LedgerEntry:
    return LedgerEntryFactory.create(
        vendor=vendor,
        kind="payout",
        amount=Decimal("-1.00"),
        external_ref=f"po_{secrets.token_hex(8)}",
    )


def external_ref_of(obj: models.Model) -> str:
    return str(obj.external_ref)  # type: ignore[attr-defined]


def vendor_with_pending_termination(vendor: Vendor) -> Vendor:
    """The vendor itself, with a pending notice of termination (CMP.5 part 4) if it has none."""
    if not TerminationNotice.objects.filter(vendor=vendor, status="pending").exists():
        TerminationNoticeFactory.create(vendor=vendor)
    return vendor


def vendor_image(vendor: Vendor) -> models.Model:
    """An image in the vendor's own collection (VEN.31)."""
    from wagtail.models import Collection

    from apps.core.tests.factories import MediaImageFactory
    from apps.vendors.engine import vendor_collection_name

    root = Collection.get_first_root_node()
    collection = root.add_child(name=vendor_collection_name(str(vendor.public_id)))
    return MediaImageFactory.create(collection=collection)


def vendor_document(vendor: Vendor) -> models.Model:
    """A PDF document in the vendor's own collection (VEN.31 part 2)."""
    from django.core.files.base import ContentFile
    from wagtail.documents import get_document_model
    from wagtail.models import Collection

    from apps.vendors.engine import vendor_collection_name
    from tests.upload_corpus import PDF

    root = Collection.get_first_root_node()
    collection = root.add_child(name=vendor_collection_name(str(vendor.public_id)))
    # A title unique per vendor, so a page that echoes the caller's own document
    # cannot be mistaken for a leak of the other vendor's (the image factory does the same).
    document = get_document_model()(title=f"Terms {vendor.public_id}", collection=collection)
    document.file.save("terms.pdf", ContentFile(PDF), save=False)
    document.save()
    return document  # type: ignore[no-any-return]


def by_pk(obj: models.Model) -> dict[str, str]:
    return {"pk": str(obj.pk)}


def by_image_id(obj: models.Model) -> dict[str, str]:
    return {"image_id": str(obj.pk)}


def vendor_display_name(vendor: models.Model) -> str:
    return str(vendor.display_name)  # type: ignore[attr-defined]


NIL_UUID = "00000000-0000-0000-0000-000000000000"


def open_draft(vendor: Vendor) -> OrderAmendment:
    """A draft amendment on the current version of one of the vendor's paid orders (ORD.20)."""
    sub = paid_vendor_order(vendor)
    assert sub.current_version is not None
    return OrderAmendmentFactory.create(from_version=sub.current_version, vendor_order=sub)


def by_amendment(obj: models.Model) -> dict[str, str]:
    amendment: OrderAmendment = obj  # type: ignore[assignment]
    return {
        "public_id": str(amendment.vendor_order.public_id),
        "amendment": str(amendment.public_id),
    }


def event_with_block(vendor: Vendor) -> EventPage:
    """An event with one body block in its latest revision (the block editor addresses it by id)."""
    page: EventPage = EventPageFactory.create(vendor=vendor)
    page.save_revision()
    return page


def by_block_id(event: models.Model) -> dict[str, str]:
    """The event's public id and the id of its first block: a real block, so the caller's own
    object does not answer 404 and the cross-tenant 404 proves the scoping."""
    assert isinstance(event, EventPage)
    latest: EventPage = EventPage.objects.get(pk=event.pk).get_latest_revision_as_object()
    return {**by_public_id(event), "block_id": str(latest.body[0].id)}


ENDPOINTS: dict[str, Access] = {
    "healthz": NoVendorData("Liveness probe with a constant answer."),
    "readyz": NoVendorData("Readiness probe: a status only, no data."),
    "robots_txt": NoVendorData("robots.txt (SEO.8): constant text for crawlers; no vendor data."),
    "favicon:ico": NoVendorData(
        "/favicon.ico (THM.38): redirect to a static theme icon; no tenant data."
    ),
    "sitemap": NoVendorData("Sitemap index (SEO.2): public page URLs only, no tenant data."),
    "sitemap_section": NoVendorData(
        "Per-language, per-type sitemap (SEO.2): public page URLs only, no tenant data.",
        kwargs={"language": "xx", "kind": "pages"},
    ),
    "sitemap_part": NoVendorData(
        "One part of a large per-language, per-type sitemap (SEO.2): public page URLs only, "
        "no tenant data.",
        kwargs={"language": "xx", "kind": "pages", "part": "0"},
    ),
    "llms_txt": NoVendorData("llms.txt (SEO.2): public page titles and URLs only, no tenant data."),
    "llms_language": NoVendorData(
        "llms.txt of one language (SEO.2): public page titles and URLs only, no tenant data.",
        kwargs={"language": "xx"},
    ),
    "email_webhook": NoVendorData("Signed email provider feedback; answers with a status only."),
    "payments_webhook": NoVendorData("Signed payment provider events; answers with a status only."),
    "media": NoVendorData(
        "Public image files (THM.12): published images and their renditions, no vendor data.",
        kwargs={"path": "images/missing.webp"},
    ),
    "palette:css": NoVendorData(
        "Public site palette stylesheet (THM.31): colors only, the same for every visitor.",
        path="/palette/default/missing.000000000000.css",
    ),
    "wagtaildocs_serve": NoVendorData(
        "Wagtail document serve view (CMS.15): library documents are not vendor data; a private "
        "collection or restricted page refuses anonymous visitors.",
        path="/documents/0/missing.pdf",
    ),
    "home": NoVendorData("Public home page."),
    "catalog:category": NoVendorData(PUBLIC_LISTING, kwargs={"segment": "product-category"}),
    "catalog:product": NoVendorData(
        "Public product page (CAT.7): only the published fields of a published product of an "
        "active vendor, the same for every visitor; Vendor B is not active, so its products are "
        "a 404.",
        kwargs={"segment": "product"},
    ),
    "catalog:brand": NoVendorData(PUBLIC_LISTING, kwargs={"segment": "brand"}),
    "catalog:term": NoVendorData(PUBLIC_LISTING, login=member_with_landing_taxonomy),
    "catalog:vendor": NoVendorData(
        "Public storefront listing (CAT.8) of an active vendor's published products; Vendor B "
        "is not active, so its storefront is a 404.",
        kwargs={"segment": "shop"},
    ),
    "switch_language": NoVendorData("Stores the visitor's language choice."),
    "consent_save": NoVendorData(
        "Cookie consent choice (SEO.5): records the caller's own decision with a random id; "
        "no vendor data."
    ),
    "consent_preferences": NoVendorData(
        "Cookie settings page (SEO.5): the caller's own choice from their own cookie; no vendor "
        "data."
    ),
    "themes:styleguide": StaffOnly("Component samples of the active theme (THM.4), staff only."),
    "site_links": StaffOnly(
        "Temporary list of the site's pages for STAGE testing (OPS.12), staff only; a signed-in "
        "non-staff user gets 404."
    ),
    "levels:options": NoVendorData(OWN_ACCOUNT),
    "levels:window": NoVendorData(OWN_ACCOUNT),
    "levels:once_options": NoVendorData(OWN_ACCOUNT),
    "levels:once": NoVendorData(OWN_ACCOUNT),
    "levels:close": NoVendorData(OWN_ACCOUNT),
    "mfa:overview": NoVendorData(OWN_ACCOUNT),
    "mfa:setup": NoVendorData(OWN_ACCOUNT),
    "mfa:totp": NoVendorData(OWN_ACCOUNT),
    "mfa:verify": NoVendorData(OWN_ACCOUNT),
    "mfa:recovery_codes": NoVendorData(OWN_ACCOUNT),
    "mfa:passkey_register_options": NoVendorData(OWN_ACCOUNT),
    "mfa:passkey_register": NoVendorData(OWN_ACCOUNT),
    "mfa:passkey_auth_options": NoVendorData(OWN_ACCOUNT),
    "mfa:passkey_auth": NoVendorData(OWN_ACCOUNT),
    "mfa:remove_device": NoVendorData(
        OWN_ACCOUNT + " The factor is looked up among the user's own.",
        kwargs={"kind": "totp", "public_id": "0"},
    ),
    "sessions:list": NoVendorData(OWN_ACCOUNT + " The user's own sessions (CORE.24)."),
    "sessions:end_others": NoVendorData(OWN_ACCOUNT + " Ends the user's own other sessions."),
    "password:change": NoVendorData(
        OWN_ACCOUNT + " Changes the user's own password after the current one (CORE.28)."
    ),
    "sessions:end_one": NoVendorData(
        OWN_ACCOUNT + " The session is looked up among the user's own (CORE.24).",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "action_link:open": NoVendorData(
        "Secure action link (CORE.15): a Continue page for a signed token; shows no object.",
        kwargs={"token": "not-a-token"},
    ),
    "action_link:continue": NoVendorData(
        "Secure action link Continue POST (CORE.15): a token of another user or object is "
        "refused before anything is shown."
    ),
    "staff_draft_link": NoVendorData(
        "Link of a cart prepared by support (ORD.32): a Continue page for a one-time token; "
        "shows no object.",
        kwargs={"token": "not-a-token"},
    ),
    "staff_draft_link_continue": NoVendorData(
        "Continue POST of a prepared cart's link (ORD.32): an unknown token or another "
        "customer's draft is refused before anything is shown."
    ),
    "vendor_terms:read": NoVendorData(
        "Sales terms in force of any seller (VEN.35, VEN.32 exception): public by design, behind "
        "a signed, expiring link; a superseded version and a tampered link are a 404, every read "
        "is logged.",
        kwargs={"token": "not-a-token"},
    ),
    "vendor_terms:pdf": NoVendorData(
        "PDF of the sales terms in force of any seller (VEN.36): public by design behind the same "
        "signed, expiring link; a superseded version and a tampered link are a 404 (an accepted-"
        "terms link opens its own version), every read is logged.",
        kwargs={"token": "not-a-token"},
    ),
    "vendor_claim:claim": NoVendorData(
        "Vendor claim page (VEN.24): without a claim link opened in the session for that vendor "
        "it shows only the generic 'link not valid' page, whoever is signed in.",
        kwargs={"vendor_key": "00000000-0000-0000-0000-000000000000"},
    ),
    "vendors:apply": NoVendorData(
        "Public 'Sell with us' application (VEN.22): shows a form and creates only the "
        "applicant's own new records; it reads no vendor data."
    ),
    "vendors:apply_sent": NoVendorData("Static 'check your email' page (VEN.22)."),
    "vendors:apply_cancel": NoVendorData(
        "Application cancel page (VEN.22): without a link opened in the session for that "
        "account it shows only a neutral page and changes nothing.",
        kwargs={"user_key": "00000000-0000-0000-0000-000000000000"},
    ),
    "vendors:apply_confirm": NoVendorData(
        "Application email confirmation (VEN.22): without a link opened in the session for "
        "that account it shows only the generic 'link not valid' page.",
        kwargs={"user_key": "00000000-0000-0000-0000-000000000000"},
    ),
    "help_entry": NoVendorData(
        "Contextual help (ARC.32): the platform's help text for a screen, the same for every "
        "signed-in caller; reads no vendor data."
    ),
    "map_tile": NoVendorData(
        "Map tile proxy (EVT.14): a tile image, the same for everyone, no vendor data.",
        kwargs={"z": "99", "x": "0", "y": "0"},
    ),
    "geocoding_find": NoVendorData(
        "Find coordinates (EVT.12): geocodes the typed address; reads no vendor data."
    ),
    # The Manager (CORE.17): one login, a shop space for vendor members (the selected vendor
    # only, chosen among the member's own vendors) and a platform space for platform staff.
    "login": NoVendorData("Manager sign-in page."),
    "logout": NoVendorData("Sign-out of the Manager."),
    "manager:shop:dashboard": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    "manager:shop:profile": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    "manager:shop:payments": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    # The image chooser of the storefront editor (VEN.31): vendor B's image id is a 404 for A.
    "manager:shop:image_chosen": VendorScoped(make=vendor_image, kwargs=by_pk),
    "manager:shop:image_select_format": VendorScoped(make=vendor_image, kwargs=by_image_id),
    "manager:shop:image_choose": VendorScopedList(
        make=vendor_image,
        identify=lambda o: str(o.title),  # type: ignore[attr-defined]
    ),
    "manager:shop:image_results": VendorScopedList(
        make=vendor_image,
        identify=lambda o: str(o.title),  # type: ignore[attr-defined]
    ),
    "manager:shop:image_create": NoVendorData(
        "The upload tab of the image chooser (VEN.31): the form and the upload answer, the file "
        "goes into the caller's own collection only; owner only, other roles get 404 "
        "(`test_staff_and_other_users_get_404...` and the upload tests in test_image_chooser)."
    ),
    # The document chooser and the link dialog of the same editor (VEN.31 part 2).
    "manager:shop:document_chosen": VendorScoped(make=vendor_document, kwargs=by_pk),
    "manager:shop:document_choose": VendorScopedList(
        make=vendor_document,
        identify=lambda o: str(o.title),  # type: ignore[attr-defined]
    ),
    "manager:shop:document_results": VendorScopedList(
        make=vendor_document,
        identify=lambda o: str(o.title),  # type: ignore[attr-defined]
    ),
    "manager:shop:document_create": NoVendorData(
        "The upload tab of the document chooser (VEN.31): the form and the upload answer, the "
        "file goes into the caller's own collection only; owner only, other roles get 404 "
        "(`test_staff_and_other_users_get_404...` in test_document_chooser)."
    ),
    "manager:shop:link_choose": NoVendorData(
        "The external link dialog (VEN.31): a form that echoes an https address back to the "
        "editor; reads and writes no vendor data; owner only "
        "(`test_staff_and_anonymous_are_refused`)."
    ),
    "manager:shop:vendor": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    # CMP.5 part 4: the notice of termination and the data download show the selected vendor only.
    "manager:shop:sales_terms": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    "manager:shop:return_policy": VendorScopedList(
        make=manager_vendor, identify=vendor_display_name
    ),
    "manager:shop:termination": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    # The statements of reasons of the selected shop (DSA art. 17); another shop's are never listed.
    "manager:shop:statements": VendorScopedList(
        make=lambda vendor: StatementOfReasonsFactory.create(vendor=vendor)
    ),
    # VEN.9: the vendor's own ledger; another vendor's entries never show or export (the provider
    # reference is the visible mark; the statement names the selected shop).
    "manager:shop:money": VendorScopedList(make=ledger_entry_with_ref, identify=external_ref_of),
    "manager:shop:money_transactions": VendorScopedList(
        make=ledger_entry_with_ref, identify=external_ref_of
    ),
    "manager:shop:money_payouts": VendorScopedList(
        make=payout_entry_with_ref, identify=external_ref_of
    ),
    "manager:shop:money_statement": VendorScopedList(
        make=manager_vendor, identify=vendor_display_name
    ),
    "manager:shop:money_export": VendorScopedList(
        make=ledger_entry_with_ref, identify=external_ref_of, methods=("POST",)
    ),
    # The download exists only while a notice is pending: the vendor made here has one.
    "manager:shop:termination_data": VendorScopedList(
        make=vendor_with_pending_termination, identify=vendor_display_name
    ),
    # ORD.6: the vendor's own orders; another vendor's id is a 404 on the detail and every action.
    "manager:shop:orders": VendorScopedList(
        make=paid_vendor_order,
        identify=lambda sub: str(sub.order.number),  # type: ignore[attr-defined]
    ),
    "manager:shop:order": VendorScoped(make=paid_vendor_order, sample={"public_id": NIL_UUID}),
    "manager:shop:order_action": VendorScoped(
        make=paid_vendor_order,
        kwargs=lambda obj: {**by_public_id(obj), "action": "accept"},
        methods=("POST",),
        sample={"public_id": NIL_UUID},
    ),
    # ORD.20 part 3: change a paid order; another vendor's package or draft is a 404.
    "manager:shop:order_amend": VendorScoped(
        make=paid_vendor_order, methods=("GET", "POST"), sample={"public_id": NIL_UUID}
    ),
    "manager:shop:amendment_apply": VendorScoped(
        make=open_draft,
        kwargs=by_amendment,
        methods=("POST",),
        sample={"public_id": NIL_UUID, "amendment": NIL_UUID},
    ),
    "manager:shop:order_cancel": VendorScoped(
        make=paid_vendor_order,
        methods=("POST",),
        sample={"public_id": NIL_UUID},
    ),
    # ORD.20 part 3b: the shop records its own invoice; another vendor's package is a 404.
    "manager:shop:order_invoice_issued": VendorScoped(
        make=paid_vendor_order,
        methods=("POST",),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:amendment_discard": VendorScoped(
        make=open_draft,
        kwargs=by_amendment,
        methods=("POST",),
        sample={"public_id": NIL_UUID, "amendment": NIL_UUID},
    ),
    # EVT.4: the vendor's own events; another vendor's id is a 404 on every view that takes one.
    # VEN.20: the shop's closures; another shop's closure is a 404 to edit or remove.
    "manager:shop:closures": VendorScopedList(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor)
    ),
    "manager:shop:closure_edit": VendorScoped(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:closure_remove": VendorScoped(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor),
        methods=("POST",),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:events": VendorScopedList(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        identify=lambda event: str(event.title),  # type: ignore[attr-defined]
    ),
    "manager:shop:event_new": NoVendorData(
        "The empty new-event form: it lists nothing; a save writes under the caller's own shop "
        "(`apps/manager/tests/integration/test_shop_events.py`)."
    ),
    "manager:shop:event_edit": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:event_submit": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        methods=("POST",),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:event_cancel": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        methods=("POST",),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:event_translate": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        sample={"public_id": NIL_UUID},
    ),
    # FIT.3: the Fits pages of the shop; every product is found through `scoped()`.
    "manager:shop:fitment": VendorScopedList(
        make=lambda vendor: ProductFactory.create(vendor=vendor),
        identify=lambda product: str(product.sku),  # type: ignore[attr-defined]
    ),
    "manager:shop:fitment_nodes": NoVendorData(
        "Picker JSON (FIT.3): the approved fitment nodes, the platform's shared vocabulary with no "
        "vendor data; members of a shop only, bounded, read-only "
        "(apps/manager/tests/integration/test_fitment_picker.py)."
    ),
    "manager:shop:fitment_product": VendorScoped(
        make=lambda vendor: ProductFactory.create(vendor=vendor),
        methods=("GET", "POST"),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:fitment_proposals": VendorScopedList(
        make=lambda vendor: FitmentNodeProposalFactory.create(vendor=vendor),
        identify=lambda proposal: str(proposal.node.name),  # type: ignore[attr-defined]
    ),
    "manager:shop:fitment_report": VendorScopedList(
        make=lambda vendor: ProductFactory.create(
            vendor=vendor, category=FitmentCategoryFactory.create().category
        ),
        identify=lambda product: str(product.sku),  # type: ignore[attr-defined]
    ),
    # EVT.4 part 2: the block editor, the preview and the image library.
    "manager:shop:event_block_add": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        methods=("GET", "POST"),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:event_block_edit": VendorScoped(
        make=event_with_block,
        kwargs=by_block_id,
        methods=("GET", "POST"),
        sample={"public_id": NIL_UUID, "block_id": NIL_UUID},
    ),
    "manager:shop:event_block_move": VendorScoped(
        make=event_with_block,
        kwargs=by_block_id,
        methods=("POST",),
        sample={"public_id": NIL_UUID, "block_id": NIL_UUID},
    ),
    "manager:shop:event_block_remove": VendorScoped(
        make=event_with_block,
        kwargs=by_block_id,
        methods=("GET", "POST"),
        sample={"public_id": NIL_UUID, "block_id": NIL_UUID},
    ),
    "manager:shop:event_preview": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        sample={"public_id": NIL_UUID},
    ),
    "manager:shop:event_images": NoVendorData(
        "The shop's own image library and upload: the images are read from and written to the "
        "caller's own vendor collection only "
        "(`apps/manager/tests/integration/test_shop_event_blocks.py`)."
    ),
    "manager:platform:dashboard": StaffOnly(
        "Manager platform space: platform staff only; a vendor member gets 404 (CORE.17)."
    ),
    "manager:platform:map": StaffOnly(
        "Platform map (CORE.30): platform staff only, links only, no tenant data; a vendor member "
        "gets the platform-space refusal (test_platform_map)."
    ),
    "manager:platform:staff_roles": StaffOnly(
        "Staff and roles (CORE.18): platform owner only; lists the platform staff and grants or "
        "revokes roles through the audited service. Every other role, a vendor member and a "
        "superuser without a role get 404 or the gate's 403 (test_staff_roles)."
    ),
    "manager:platform:user_sessions": StaffOnly(
        "User sessions (CORE.24): Support and the platform owner only; a vendor member gets the "
        "platform gate's refusal and other roles 404 (test_session_control)."
    ),
    "manager:platform:user_sessions_find": StaffOnly(
        "Account lookup of User sessions (CORE.24): Support and the platform owner only, POST "
        "with CSRF so the email never reaches a URL; redirects with the public id."
    ),
    "manager:platform:sessions_panic": StaffOnly(
        "End every session (CORE.24): the platform owner only; Support, other roles and a "
        "vendor member are refused (test_session_control)."
    ),
    "manager:platform:profile_changes": StaffOnly(
        "Storefront profile history (CORE.9): platform staff holding the revert permission only; "
        "a vendor member gets 403 on the platform space, so no vendor sees any history."
    ),
    "manager:platform:profile_confirm": StaffOnly(
        "Confirm of one logo change (CORE.9): platform staff holding the revert permission only, "
        "POST only.",
        kwargs={"entry": "00000000-0000-0000-0000-000000000000"},
    ),
    "manager:platform:profile_revert": StaffOnly(
        "Revert of one storefront profile change (CORE.9): platform staff holding the revert "
        "permission only, POST only.",
        kwargs={"entry": "00000000-0000-0000-0000-000000000000"},
    ),
    "manager:platform:notices": StaffOnly(
        "Notices of illegal content (CMP.5): platform data, not vendor-owned; needs the "
        "compliance.review_notices permission, so a vendor member gets 404."
    ),
    "manager:platform:notice": StaffOnly(
        "One notice of illegal content (CMP.5): platform data, not vendor-owned; needs the "
        "compliance.review_notices permission, so a vendor member gets 404.",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "garage_save": NoVendorData(
        "Anonymous garage cookie control (FIT.6): reads and writes only the visitor's own cookie "
        "and the vehicle scheme; no vendor data."
    ),
    "garage_remove": NoVendorData(
        "Anonymous garage cookie control (FIT.6): reads and writes only the visitor's own cookie "
        "and the vehicle scheme; no vendor data."
    ),
    "garage_forget": NoVendorData(
        "Anonymous garage cookie control (FIT.6): reads and writes only the visitor's own cookie "
        "and the vehicle scheme; no vendor data."
    ),
    "garage_choose": NoVendorData(
        "The garage chooser (FIT.7): lists the visitor's own cookie vehicles or own profiles; "
        "no vendor data."
    ),
    "garage_apply": NoVendorData(
        "The garage chooser's Apply (FIT.7): writes the visitor's own cookie or activates their "
        "own profile (another account's is a 404); no vendor data."
    ),
    "garage_line_dismiss": NoVendorData(
        "The Add your garage line's Dismiss (FIT.7): sets one flag in the visitor's own session; "
        "no vendor data."
    ),
    "garage_merge": NoVendorData(
        "The sign-in notice's Add and Not now (FIT.7): a signed-in customer copies the vehicles of "
        "their own browser cookie into their own profiles; no vendor data."
    ),
    "manager:platform:orders": StaffOnly(
        "Order changes (ORD.20 part 3): Support and the platform owner find an order by number; "
        "a vendor member gets the platform gate's refusal (test_amendment_views)."
    ),
    "manager:platform:order": StaffOnly(
        "One package's staff tools (ORD.20 part 3): Support and the platform owner only; a "
        "vendor member gets the platform gate's refusal (test_amendment_views).",
        kwargs={"public_id": NIL_UUID},
    ),
    "manager:platform:amendment_apply": StaffOnly(
        "Apply a staff draft (ORD.20 part 3): Support and the platform owner only, POST only.",
        kwargs={"public_id": NIL_UUID, "amendment": NIL_UUID},
    ),
    "manager:platform:amendment_discard": StaffOnly(
        "Discard a staff draft (ORD.20 part 3): Support and the platform owner only, POST only.",
        kwargs={"public_id": NIL_UUID, "amendment": NIL_UUID},
    ),
    "manager:platform:order_cancel": StaffOnly(
        "Cancel a package with a full refund (ORD.20 part 3): Support and the platform owner "
        "only, POST only, with a note and a fresh passkey.",
        kwargs={"public_id": NIL_UUID},
    ),
    "manager:platform:stray_payments": StaffOnly(
        "Stray payments (PAY.4): Support and the platform owner close, Finance reads; a close by "
        "Finance answers 403, other roles and a vendor member 404 (test_stray_payments). The "
        "alerts span shops by design and show references and amounts only."
    ),
    # VEN.20 slice 3b: Support and the platform owner manage any shop's closures.
    "manager:platform:shop_closures": StaffOnly(
        "Shop picker (VEN.20): Support and the platform owner; a vendor member gets the platform "
        "gate's 403 and other roles 404. It lists shop names only."
    ),
    "manager:platform:shop_closures_vendor": StaffOnly(
        "One shop's closures, named by the URL (VEN.20): staff span shops by design; an unknown "
        "shop is a 404.",
        kwargs={"vendor_id": NIL_UUID},
    ),
    "manager:platform:shop_closure_edit": StaffOnly(
        "Edit a closure (VEN.20): the closure must belong to the shop in the URL, else 404.",
        kwargs={"vendor_id": NIL_UUID, "public_id": NIL_UUID},
    ),
    "manager:platform:shop_closure_remove": StaffOnly(
        "Remove a closure (VEN.20): the closure must belong to the shop in the URL, else 404.",
        kwargs={"vendor_id": NIL_UUID, "public_id": NIL_UUID},
    ),
    "manager:platform:order_drafts": StaffOnly(
        "Prepared carts (ORD.32): Support and the platform owner only; a vendor member gets the "
        "platform gate's refusal and other roles 404 (test_staff_drafts)."
    ),
    "manager:platform:order_draft_new": StaffOnly(
        "Prepare a cart from an order (ORD.32): Support and the platform owner only, POST only, "
        "with a note.",
        kwargs={"order": NIL_UUID},
    ),
    "manager:platform:order_draft": StaffOnly(
        "One prepared cart (ORD.32): Support and the platform owner only.",
        kwargs={"public_id": NIL_UUID},
    ),
    "manager:platform:order_draft_lines": StaffOnly(
        "Change a prepared cart's quantities (ORD.32): Support and the platform owner only, POST "
        "only, with a note; never a price.",
        kwargs={"public_id": NIL_UUID},
    ),
    "manager:platform:order_draft_send": StaffOnly(
        "Send a prepared cart's one-time link (ORD.32): Support and the platform owner only, POST "
        "only, to the order's own account or guest email.",
        kwargs={"public_id": NIL_UUID},
    ),
    "manager:platform:amendment_settings": StaffOnly(
        "The amendment settings in force (ORD.20 part 3): platform configuration, no vendor "
        "data; the configuration role and the platform owner only."
    ),
    "manager:platform:trust_rules": StaffOnly(
        "The trust rules versions (TRU.1 part 2): platform configuration, no vendor data; the "
        "configuration role and the platform owner only."
    ),
    "manager:platform:trust_rule": StaffOnly(
        "One trust rules version (TRU.1 part 2): platform configuration, no vendor data; the "
        "configuration role and the platform owner only.",
        kwargs={"version": "1"},
    ),
    "manager:platform:trust_rule_new": StaffOnly(
        "A new trust rules version with a dry-run preview (TRU.1 part 2): platform configuration, "
        "no vendor data; the configuration role and the platform owner only; the POST needs the "
        "operator_config session level."
    ),
    "manager:platform:terminations": StaffOnly(
        "Notices of termination (CMP.5 part 4): platform data; needs the "
        "compliance.review_notices permission, so a vendor member gets 404."
    ),
    "manager:platform:termination": StaffOnly(
        "One notice of termination (CMP.5 part 4): needs the compliance.review_notices "
        "permission, so a vendor member gets 404.",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "manager:platform:fitment_scheme": StaffOnly(
        "Fitment scheme (FIT.1): platform data, one scheme per installation, no vendor data; "
        "Configuration and tax writes, the owner reads, every other role and a vendor member get "
        "404 (apps/manager/tests/integration/test_fitment_pages.py)."
    ),
    "manager:platform:fitment_categories": StaffOnly(
        "Categories expected to declare fitment (FIT.1): platform data, no vendor data; "
        "Configuration and tax writes, Catalog and the owner read, a vendor member gets 404."
    ),
    "manager:platform:fitment_nodes": StaffOnly(
        "Fitment nodes (FIT.1): the shared vocabulary, not vendor-owned; Catalogue and moderation "
        "writes, Support and the owner read, a vendor member gets 404."
    ),
    "manager:platform:fitment_proposals": StaffOnly(
        "Proposed fitment nodes (FIT.1): the review queue of the Catalogue and moderation role; a "
        "vendor member gets 404."
    ),
    "manager:platform:fitment_node_new": StaffOnly(
        "Add a fitment node (FIT.1): Catalogue and moderation only; a vendor member gets 404."
    ),
    "manager:platform:fitment_node": StaffOnly(
        "One fitment node (FIT.1): the shared vocabulary, not vendor-owned; Catalog and "
        "moderation writes, a vendor member gets 404.",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "manager:platform:fitment_node_merge": StaffOnly(
        "Merge of one fitment node (FIT.1): Catalogue and moderation only, shown as a dry run "
        "first; a vendor member gets 404.",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "manager:platform:catalog_products": StaffOnly(
        "Product list of the platform (CAT.11): Catalogue and moderation writes, the owner reads, "
        "Finance, Support and a vendor member get 404; rows come from for_actor."
    ),
    "manager:platform:catalog_product": StaffOnly(
        "One product, preview and audited fields (CAT.11): same roles as the list.",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "subprocessors": NoVendorData(
        "Sub-processor register (CMP.4): static reviewed data, the same for every visitor."
    ),
    "privacy_account": NoVendorData(
        "The signed-in user's own privacy page (CMP.4): forms only, no vendor data."
    ),
    "privacy_export": NoVendorData(
        "Own-data export (CMP.4): POST only, always the signed-in user, never an id from the "
        "input; fresh re-authentication and a rate limit guard it."
    ),
    "privacy_erase": NoVendorData(
        "Own account erasure (CMP.4, D-30): POST only, always the signed-in user; fresh "
        "re-authentication, confirmation and a rate limit guard it."
    ),
    "privacy_erase_done": NoVendorData(
        "Static confirmation page after an erasure request (CMP.4): no data."
    ),
    "garage_account": NoVendorData(
        "The signed-in customer's own garage profiles (FIT.5): the list starts from the caller's "
        "profiles (`test_the_page_lists_the_profiles_and_marks_the_active_one`); no vendor data."
    ),
    "garage_reset": NoVendorData(
        "The signed-in customer switches their own active profile off (FIT.5): POST only, always "
        "the caller's profiles (`test_reset_leaves_another_customers_garage_alone`)."
    ),
    "garage_profile_activate": NoVendorData(
        "The signed-in customer makes one of their own profiles active (FIT.5): the profile is "
        "looked up among the caller's own, "
        "anything else is a 404 "
        "(`test_another_customers_profile_is_a_404_like_an_unknown_one`).",
        kwargs={"public_id": NIL_UUID},
    ),
    "garage_profile_rename": NoVendorData(
        "The signed-in customer renames one of their own profiles (FIT.5): the profile is "
        "looked up among the caller's own, "
        "anything else is a 404 "
        "(`test_another_customers_profile_is_a_404_like_an_unknown_one`).",
        kwargs={"public_id": NIL_UUID},
    ),
    "garage_profile_duplicate": NoVendorData(
        "The signed-in customer duplicates one of their own profiles (FIT.5): the profile is "
        "looked up among the caller's own, "
        "anything else is a 404 "
        "(`test_another_customers_profile_is_a_404_like_an_unknown_one`).",
        kwargs={"public_id": NIL_UUID},
    ),
    "garage_profile_delete": NoVendorData(
        "The signed-in customer deletes one of their own profiles after a "
        "confirmation step (FIT.5): the profile is "
        "looked up among the caller's own, "
        "anything else is a 404 "
        "(`test_another_customers_profile_is_a_404_like_an_unknown_one`).",
        kwargs={"public_id": NIL_UUID},
    ),
    "customer_orders": NoVendorData(
        "The signed-in customer's own orders (ORD.7): the queryset starts from the caller's "
        "orders (`test_the_list_shows_only_the_callers_orders`); a vendor part shows the vendor's "
        "public display name only, never its members, commission or notes."
    ),
    "customer_order": NoVendorData(
        "One of the signed-in customer's own orders (ORD.7): a stranger's order, a guest order "
        "and an unknown id all answer 404 "
        "(`test_a_stranger_s_order_is_a_404_like_an_unknown_one`).",
        kwargs={"public_id": NIL_UUID},
    ),
    "customer_order_cancel_rest": NoVendorData(
        "The signed-in customer cancels the rest of one of their own packages (ORD.20): the "
        "order is looked up among the caller's own and the package within it; anything else is "
        "a 404 (`test_a_stranger_cannot_cancel_the_rest`).",
        kwargs={"public_id": NIL_UUID, "package": NIL_UUID},
    ),
    "customer_order_keep_rest": NoVendorData(
        "The signed-in customer keeps the rest of one of their own packages (ORD.20): looked up "
        "like the cancel; anything else is a 404 (`test_a_stranger_cannot_cancel_the_rest`).",
        kwargs={"public_id": NIL_UUID, "package": NIL_UUID},
    ),
    "customer_order_again": NoVendorData(
        "The signed-in customer puts one of their own past orders back in their cart (ORD.31): "
        "the order is looked up among the caller's own; anything else is a 404 "
        "(`test_another_customers_order_is_a_404_also_by_guessing`).",
        kwargs={"public_id": NIL_UUID},
    ),
    "privacy_requests": StaffOnly(
        "Erasure requests (CMP.4): platform data listed by public id; needs "
        "compliance.change_erasurerequest, so a vendor member gets 404."
    ),
    "privacy_request_process": StaffOnly(
        "Process an erasure request (CMP.4): platform data; needs "
        "compliance.change_erasurerequest, so a vendor member gets 404.",
        kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
    ),
    "dsa_notice": NoVendorData(
        "Public report form for illegal content (DSA art. 16, CMP.5): no vendor data is shown, "
        "the form only stores a notice; captcha and a rate limit guard the POST."
    ),
    "dsa_notice_received": NoVendorData(
        "Thanks page after a report (CMP.5): shows only the reference kept in the session."
    ),
    "orders:cart": NoVendorData(
        "Cart page (ORD.5): shows the caller's own cart, found from their session or account, "
        "never a cart or a vendor's data chosen by an identifier."
    ),
    "orders:cart_mini": NoVendorData(
        "Mini-cart fragment (THM.15): the caller's own cart lines, found from their session or "
        "account; no vendor data is shown."
    ),
    "orders:cart_add": NoVendorData(
        "Add to cart (ORD.19): adds a public product to the caller's own cart, found from their "
        "session or account; it reads and shows no vendor data."
    ),
    "orders:checkout": NoVendorData(
        "Checkout details (ORD.5): the caller's own cart and their own typed details only."
    ),
    "orders:review": NoVendorData(
        "Checkout review (ORD.5): the caller's own cart and session; no identifier in the URL."
    ),
    "orders:shipping": NoVendorData(
        "Shipping choice (ORD.5): stores a choice in the caller's own session for their own cart."
    ),
    "orders:confirmation": NoVendorData(
        "Order confirmation (ORD.5): shows only the order the session just placed, a 404 without."
    ),
    "withdrawal": NoVendorData(
        "Online withdrawal, step one (CMP.6): no vendor data without the order number and email "
        "of a contract; captcha and a rate limit guard the POST."
    ),
    "withdrawal_confirm": NoVendorData(
        "Online withdrawal, step two (CMP.6): POST only, answers 405 to a GET; a signed token "
        "names one order and every chosen vendor order must belong to it."
    ),
    "withdrawal_done": NoVendorData(
        "Withdrawal acknowledgement (CMP.6): shows only the statements kept in the session."
    ),
    "consumer_information": NoVendorData(
        "Pre-contractual information (CMP.6): platform-wide legal text, no vendor data."
    ),
    "withdrawal_form_pdf": NoVendorData(
        "Blank model withdrawal form PDF (I18N.20): platform-wide legal text, no vendor data."
    ),
    "customer_help": NoVendorData("Customer help index (ARC.37): repository text, no vendor data."),
    "customer_help_section": NoVendorData(
        "Customer help section (ARC.37): repository text, no vendor data.",
        kwargs={"section": "ordering"},
    ),
    "vendor_help": NoVendorData("Seller guide index (ARC.37): repository text, no vendor data."),
    "vendor_help_section": NoVendorData(
        "Seller guide section (ARC.37): repository text, no vendor data.",
        kwargs={"section": "getting-started"},
    ),
    "admin:login": NoVendorData("Staff sign-in page."),
    "admin:logout": NoVendorData("Staff sign-out."),
    "admin:jsi18n": NoVendorData("Admin JavaScript translation catalog."),
    "api-v1:api-root": NoVendorData("API root: links to the API description only."),
    "api-v1:openapi-json": NoVendorData("API description, the same for every caller."),
    "api-v1:openapi-view": NoVendorData("Interactive API documentation page."),
    "api/v1/": NoVendorData(
        "Unknown API paths: a 404 problem document.", path="/api/v1/no-such-operation"
    ),
    # CMS (CMS.1): public pages and the editors' sign-in. The rest of /admin/ is CMS_ADMIN.
    "dsa_contact_points": NoVendorData(
        "DSA points of contact (CMP.5): the platform operator's own contact data, the same for "
        "every visitor; no vendor data."
    ),
    "events_resource": NoVendorData(
        "File-like routes of public event pages (EVT.6, EVT.9): the single-event ICS and the RSS "
        "and ICS feeds (platform, vendor, type), the same for every visitor; hidden events are a "
        "404 and in no feed.",
        kwargs={"path": "events/no-such-event/calendar.ics"},
    ),
    "wagtail_serve": NoVendorData(
        "Public CMS pages (home, static pages); no vendor data until the CMS.4 landing pages.",
        path="/no-such-page/",
    ),
    "wagtailcore_login": NoVendorData("Sign-in for password-protected pages."),
    "wagtailcore_authenticate_with_password": NoVendorData("Password form of a private page."),
    "wagtailadmin_login": NoVendorData("Editors' sign-in page."),
    "wagtailadmin_sprite": NoVendorData("The admin's icon sprite, the same for everyone."),
    "wagtailadmin_javascript_catalog": NoVendorData("Admin JavaScript translation catalog."),
}

ADMIN_NAMESPACE = "admin"
ADMIN_REASON = "Django admin: technical superuser and platform staff only (spec 02 §2.2)."
ADMIN_SAMPLE_KWARGS = {"app_label": "vendors", "url": "missing/"}


def _admin_model(endpoint: Endpoint) -> type[models.Model] | None:
    route = "/" + endpoint.route
    for model in admin.site._registry:
        if f"/{model._meta.app_label}/{model._meta.model_name}/" in route:
            return model
    return None


# Wagtail admin routes replaced by a project view on the same path, included ahead of Wagtail's
# admin URLs, under a URL name of its own: shadowed key -> key of the view that answers instead.
# The shadowed pattern is never reached, so it gets no request of its own; the replacement is
# an endpoint like any other and is checked under its own key (staff only, as CMS_ADMIN).
# A replacement keeps Wagtail's name when it can (apps.cms.admin_urls); a namespaced name cannot
# be kept, as a second include with Wagtail's namespace would take over its reverse().
SHADOWED: dict[str, str] = {
    # THM.12: collection edit refuses moving a collection that holds SVGs out of the SVG tree.
    "wagtailadmin_collections:edit": "core_collection_edit",
}

CMS_ADMIN_ROUTE = "admin/"
CMS_ADMIN = StaffOnly(
    "Wagtail admin: editors and staff holding `wagtailadmin.access_admin` only (CMS.1); "
    "it shows pages and menus, no vendor-owned model yet."
)


def classify(endpoint: Endpoint) -> Access | None:
    """The declared access of a web endpoint; None when it is not declared."""
    if endpoint.key in ENDPOINTS:
        return ENDPOINTS[endpoint.key]
    if endpoint.route.startswith(CMS_ADMIN_ROUTE):
        return CMS_ADMIN
    if endpoint.namespace == ADMIN_NAMESPACE:
        model = _admin_model(endpoint)
        make = OWNED_OBJECTS.get(model) if model is not None else None
        return StaffOnly(ADMIN_REASON, make=make, kwargs=ADMIN_SAMPLE_KWARGS)
    return None


# --- API v1 operations ("METHOD /path" as in the OpenAPI description), called with Vendor A's
# API key (CORE.13)


def active_vendor_events_key(client: Client, tenants: CrossTenant) -> dict[str, str]:
    """A write key is refused (403) while its vendor is not active (CORE.14): activate Vendor A."""
    Vendor.objects.filter(pk=tenants.a.vendor.pk).update(status=Vendor.Status.ACTIVE)
    tenants.a.vendor.refresh_from_db()
    return vendor_api_key(["events:read", "events:write"])(client, tenants)


def active_closures_key(client: Client, tenants: CrossTenant) -> dict[str, str]:
    """A write key is refused (403) while its vendor is not active (CORE.14): activate Vendor A."""
    Vendor.objects.filter(pk=tenants.a.vendor.pk).update(status=Vendor.Status.ACTIVE)
    tenants.a.vendor.refresh_from_db()
    return vendor_api_key(["closures:read", "closures:write"])(client, tenants)


def closure_kwargs(obj: models.Model) -> dict[str, str]:
    """The URL parameters that name a closure: its vendor's public id and its own."""
    assert isinstance(obj, VendorClosure)
    return {"vendor_id": str(obj.vendor.public_id), "closure_id": str(obj.public_id)}


API_OPERATIONS: dict[str, Access] = {
    "GET /api/v1/me": NoVendorData("The calling API client itself, with its own vendor."),
    # CAT.3
    "GET /api/v1/catalog/attributes": NoVendorData(
        "The platform's attribute catalogue (codes, kinds, value labels): shared by every vendor."
    ),
    "POST /api/v1/catalog/products": NoVendorData(
        "Creates a product for the caller's own vendor; a vendor key naming another vendor gets "
        "404. Shows only the ChangeSet of what it creates."
    ),
    "GET /api/v1/catalog/products/{public_id}": VendorScoped(
        make=lambda vendor: ProductFactory.create(vendor=vendor)
    ),
    # CAT.15
    "GET /api/v1/catalog/products/{public_id}/other-sellers": VendorScoped(
        make=lambda vendor: ProductFactory.create(vendor=vendor)
    ),
    # EVT.5
    "GET /api/v1/events": VendorScopedList(
        make=lambda vendor: EventPageFactory.create(vendor=vendor)
    ),
    "POST /api/v1/events": NoVendorData(
        "Creates a draft for the caller's own vendor; a platform key is refused. Shows only the "
        "ChangeSet of what it creates."
    ),
    "GET /api/v1/events/{public_id}": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor)
    ),
    "PATCH /api/v1/events/{public_id}": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        login=active_vendor_events_key,
    ),
    "POST /api/v1/events/{public_id}/submit": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        login=active_vendor_events_key,
    ),
    "POST /api/v1/events/{public_id}/cancel": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        login=active_vendor_events_key,
    ),
    "POST /api/v1/events/{public_id}/translations": VendorScoped(
        make=lambda vendor: EventPageFactory.create(vendor=vendor),
        login=active_vendor_events_key,
    ),
    # VEN.20: the closures of a vendor. Vendor A's key names Vendor B's id and closure: a 404.
    "GET /api/v1/vendors/{vendor_id}/closures": VendorScoped(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor),
        login=active_closures_key,
        kwargs=closure_kwargs,
        sample={"vendor_id": NIL_UUID},
    ),
    "POST /api/v1/vendors/{vendor_id}/closures": VendorScoped(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor),
        login=active_closures_key,
        kwargs=closure_kwargs,
        sample={"vendor_id": NIL_UUID},
    ),
    "PATCH /api/v1/vendors/{vendor_id}/closures/{closure_id}": VendorScoped(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor),
        login=active_closures_key,
        kwargs=closure_kwargs,
        sample={"vendor_id": NIL_UUID, "closure_id": NIL_UUID},
    ),
    "DELETE /api/v1/vendors/{vendor_id}/closures/{closure_id}": VendorScoped(
        make=lambda vendor: VendorClosureFactory.create(vendor=vendor),
        login=active_closures_key,
        kwargs=closure_kwargs,
        sample={"vendor_id": NIL_UUID, "closure_id": NIL_UUID},
    ),
}
