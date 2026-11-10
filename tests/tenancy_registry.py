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

from django.contrib import admin
from django.db import models
from django.test import Client

from apps.api.models import ApiClient, ApiRateCounter
from apps.api.tests.factories import ApiClientFactory, ApiRateCounterFactory
from apps.cart.models import CartLine
from apps.cart.tests.factories import CartLineFactory
from apps.catalog.models import (
    Product,
    ProductAttribute,
    ProductTranslation,
    SkuAlias,
    SlugHistory,
    Variant,
    VariantOption,
    VariantTranslation,
)
from apps.catalog.tests.factories import (
    ProductAttributeFactory,
    ProductFactory,
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
from apps.compliance.models import GpsrInfo, GpsrSafetyText, SafetyCase, VendorGpsrDefaults
from apps.compliance.tests.factories import (
    GpsrInfoFactory,
    GpsrSafetyTextFactory,
    SafetyCaseFactory,
    VendorGpsrDefaultsFactory,
)
from apps.core.models import TranslationStatus
from apps.core.tests.factories import utc
from apps.events.models import EventPage, VendorEventPolicy
from apps.events.tests.factories import EventPageFactory, VendorEventPolicyFactory
from apps.orders.models import OrderLine, VendorOrder
from apps.orders.tests.factories import OrderLineFactory, VendorOrderFactory
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
from apps.vendors.models import (
    ContractAcceptance,
    CrossVendorConsent,
    SelfCertification,
    Vendor,
    VendorClaim,
    VendorFeatureFlag,
    VendorMember,
    VendorTranslation,
)
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    CrossVendorConsentFactory,
    SelfCertificationFactory,
    VendorClaimFactory,
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
    VendorFeatureFlag: lambda vendor: VendorFeatureFlagFactory.create(vendor=vendor),
    CrossVendorConsent: lambda vendor: CrossVendorConsentFactory.create(vendor=vendor),
    VendorClaim: lambda vendor: VendorClaimFactory.create(vendor=vendor),
    Product: lambda vendor: ProductFactory.create(vendor=vendor),
    ProductTranslation: lambda vendor: ProductTranslationFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    SlugHistory: lambda vendor: SlugHistoryFactory.create(
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
    SkuAlias: lambda vendor: renamed_product_alias(vendor),
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
    GpsrSafetyText: lambda vendor: GpsrSafetyTextFactory.create(
        product=ProductFactory.create(vendor=vendor)
    ),
    SafetyCase: lambda vendor: SafetyCaseFactory.create(
        product=ProductFactory.create(vendor=vendor)
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
    OrderLine: lambda vendor: OrderLineFactory.create(vendor_order__vendor=vendor),
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


def vendor_display_name(vendor: models.Model) -> str:
    return str(vendor.display_name)  # type: ignore[attr-defined]


ENDPOINTS: dict[str, Access] = {
    "healthz": NoVendorData("Liveness probe with a constant answer."),
    "robots_txt": NoVendorData("robots.txt (SEO.8): constant text for crawlers; no vendor data."),
    "email_webhook": NoVendorData("Signed email provider feedback; answers with a status only."),
    "media": NoVendorData(
        "Public image files (THM.12): published images and their renditions, no vendor data.",
        kwargs={"path": "images/missing.webp"},
    ),
    "home": NoVendorData("Public home page."),
    "catalog:category": NoVendorData(PUBLIC_LISTING, kwargs={"segment": "product-category"}),
    "catalog:brand": NoVendorData(PUBLIC_LISTING, kwargs={"segment": "brand"}),
    "catalog:term": NoVendorData(PUBLIC_LISTING, login=member_with_landing_taxonomy),
    "catalog:vendor": NoVendorData(
        "Public storefront listing (CAT.8) of an active vendor's published products; Vendor B "
        "is not active, so its storefront is a 404.",
        kwargs={"segment": "shop"},
    ),
    "switch_language": NoVendorData("Stores the visitor's language choice."),
    "themes:styleguide": StaffOnly("Component samples of the active theme (THM.4), staff only."),
    "levels:options": NoVendorData(OWN_ACCOUNT),
    "levels:operational": NoVendorData(OWN_ACCOUNT),
    "levels:once_options": NoVendorData(OWN_ACCOUNT),
    "levels:once": NoVendorData(OWN_ACCOUNT),
    "levels:end": NoVendorData(OWN_ACCOUNT),
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
    "action_link:open": NoVendorData(
        "Secure action link (CORE.15): a Continue page for a signed token; shows no object.",
        kwargs={"token": "not-a-token"},
    ),
    "action_link:continue": NoVendorData(
        "Secure action link Continue POST (CORE.15): a token of another user or object is "
        "refused before anything is shown."
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
    "geocoding_find": NoVendorData(
        "Find coordinates (EVT.12): geocodes the typed address; reads no vendor data."
    ),
    # The Manager (CORE.17): one login, a shop space for vendor members (the selected vendor
    # only, chosen among the member's own vendors) and a platform space for platform staff.
    "login": NoVendorData("Manager sign-in page."),
    "logout": NoVendorData("Sign-out of the Manager."),
    "manager:shop:dashboard": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    "manager:shop:profile": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    "manager:shop:vendor": VendorScopedList(make=manager_vendor, identify=vendor_display_name),
    "manager:platform:dashboard": StaffOnly(
        "Manager platform space: platform staff only; a vendor member gets 404 (CORE.17)."
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
}
