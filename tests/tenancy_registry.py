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

from apps.api.models import ApiClient
from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Product, ProductTranslation, Variant, VariantTranslation
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.tests.factories import utc
from apps.shipping.models import PickupOption, PickupOptionTranslation, VendorZoneRate, WeightBand
from apps.shipping.tests.factories import (
    PickupOptionFactory,
    PickupOptionTranslationFactory,
    VendorZoneRateFactory,
    WeightBandFactory,
)
from apps.tax.models import VendorTaxProfile, VendorTaxProfileTranslation
from apps.tax.tests.factories import VendorTaxProfileFactory, VendorTaxProfileTranslationFactory
from apps.vendors.models import ContractAcceptance, Vendor, VendorMember, VendorTranslation
from apps.vendors.tests.factories import (
    ContractAcceptanceFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)
from tests.tenancy import (
    Access,
    Endpoint,
    NoVendorData,
    ObjectMaker,
    StaffOnly,
)

# --- Vendor-owned models: how to make one object owned by a given vendor

OWNED_OBJECTS: dict[type[models.Model], ObjectMaker] = {
    Vendor: lambda vendor: vendor,
    ApiClient: lambda vendor: ApiClientFactory.create(vendor=vendor),
    VendorMember: lambda vendor: VendorMemberFactory.create(
        vendor=vendor, role=VendorMember.Role.STAFF
    ),
    VendorTranslation: lambda vendor: VendorTranslationFactory.create(vendor=vendor, language="it"),
    ContractAcceptance: lambda vendor: ContractAcceptanceFactory.create(vendor=vendor),
    Product: lambda vendor: ProductFactory.create(vendor=vendor),
    ProductTranslation: lambda vendor: ProductTranslationFactory.create(
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
    VendorZoneRate: lambda vendor: VendorZoneRateFactory.create(vendor=vendor),
    WeightBand: lambda vendor: WeightBandFactory.create(
        rate=VendorZoneRateFactory.create(vendor=vendor)
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
}

# Models with a ForeignKey chain to Vendor that are deliberately not vendor-scoped, by label,
# with the reason. Empty: every vendor-owned model is scoped.
NOT_VENDOR_OWNED: dict[str, str] = {}


# --- Web endpoints (URL name, or route when unnamed)

OWN_ACCOUNT = "The signed-in user's own second factors (CORE.4); no vendor data."

ENDPOINTS: dict[str, Access] = {
    "healthz": NoVendorData("Liveness probe with a constant answer."),
    "home": NoVendorData("Public home page."),
    "switch_language": NoVendorData("Stores the visitor's language choice."),
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
    "admin:login": NoVendorData("Staff sign-in page."),
    "admin:logout": NoVendorData("Staff sign-out."),
    "admin:jsi18n": NoVendorData("Admin JavaScript translation catalog."),
    "api-v1:api-root": NoVendorData("API root."),
    "api-v1:openapi-json": NoVendorData("API description, the same for every caller."),
    "api-v1:openapi-view": NoVendorData("Interactive API documentation page."),
    "api/v1/": NoVendorData(
        "Unknown API paths: a 404 problem document.", path="/api/v1/no-such-operation"
    ),
    # CMS (CMS.1): public pages and the editors' sign-in. The rest of /admin/ is CMS_ADMIN.
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

API_OPERATIONS: dict[str, Access] = {
    "GET /api/v1/me": NoVendorData("The calling API client itself, with its own vendor."),
}
