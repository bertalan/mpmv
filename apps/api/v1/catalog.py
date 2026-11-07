"""Catalog operations of API v1 (CAT.3): attributes, and products with variant axes and variants.

Thin adapters over `apps.catalog.services`: the payload becomes `engine.ProductIn`, the service
validates, applies or dry-runs, and audits. Vendor data is reached through the caller's actor
scope (CORE.13): a vendor key creates and reads its own vendor's products only, and naming
another vendor answers 404, as for a product that does not exist.

Amounts travel as JSON strings ("19.90") or numbers and become `Decimal`; prices are checked
by the service (2 decimals, not negative).
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from django.conf import settings
from django.http import HttpRequest
from ninja import Query, Router, Schema, Status
from pydantic import Field, field_validator

from apps.api.auth import api_client_of
from apps.api.v1.errors import ApiProblem
from apps.catalog import engine, services
from apps.catalog.models import Product, ProductAttribute, Variant
from apps.vendors.models import Vendor
from apps.vendors.scoping import get_for_actor_or_404

from .mutations import ChangeSetOut, DryRun, audit_context, changeset_out
from .security import ApiKey

router = Router(tags=["catalog"])

READ = "catalog:read"
WRITE = "catalog:write"


def _language(value: str) -> str:
    if value not in {code for code, _ in settings.LANGUAGES}:
        raise ValueError(f"{value!r} is not a configured language")
    return value


class LanguageQuery(Schema):
    language: str = Field(
        "en", description="Language of names and labels; EN is the fallback (spec 09)."
    )

    @field_validator("language")
    @classmethod
    def _known(cls, value: str) -> str:
        return _language(value)


# --- Attribute catalogue


class ValueOut(Schema):
    value: str
    label: str


class AttributeOut(Schema):
    code: str
    kind: str
    unit: str
    name: str
    values: list[ValueOut]


@router.get(
    "/attributes", response=list[AttributeOut], auth=ApiKey(READ), summary="Attribute catalogue"
)
def list_attributes(
    request: HttpRequest, query: Annotated[LanguageQuery, Query()]
) -> list[dict[str, Any]]:
    """The platform's attributes with their kind, unit, values and translated labels: the codes
    to send when creating a product."""
    language = query.language
    return [
        {
            "code": attribute.code,
            "kind": attribute.kind,
            "unit": attribute.unit,
            "name": services.attribute_name(attribute, language),
            "values": [
                {"value": value.value, "label": services.value_label(value, language)}
                for value in attribute.values.all()
            ],
        }
        for attribute in services.attribute_catalogue()
    ]


# --- Products


class TranslationIn(Schema):
    language: str
    title: str = Field(min_length=1, max_length=255)
    short_description: str = ""
    description: str = ""


class ProductAttributeIn(Schema):
    attribute: str = Field(description="Attribute code, e.g. size.")
    values: list[str] = Field(default_factory=list, description="Value codes (choice attributes).")
    number: Decimal | None = Field(None, description="Number attributes, in the attribute's unit.")
    text: str = Field("", max_length=255, description="Text attributes (language-neutral).")
    variant_axis: bool = Field(
        False, description="The variants vary on it; axes are ordered as listed."
    )


class VariantIn(Schema):
    sku: str = Field(min_length=1, max_length=engine.SKU_MAX_LENGTH)
    options: dict[str, str] = Field(description="Value code per variant axis, e.g. {size: m}.")
    price_net_override: Decimal | None = None
    weight_kg_override: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=3)
    stock_qty: int = Field(0, ge=0)
    is_default: bool = False


class ProductIn(Schema):
    vendor: uuid.UUID | None = Field(
        None, description="Required with a platform key; a vendor key may only name its own."
    )
    sku: str = Field(min_length=1, max_length=engine.SKU_MAX_LENGTH)
    kind: Literal["simple", "variable"] = "simple"
    sales_mode: Literal["buy", "catalog_only", "on_request"] = "buy"
    fiscal_category: Literal["standard", "reduced", "book_art74"] = "standard"
    price_net: Decimal = Field(description="EUR, VAT excluded.")
    weight_kg: Decimal | None = Field(None, ge=0, max_digits=8, decimal_places=3)
    translations: list[TranslationIn] = Field(default_factory=list)
    attributes: list[ProductAttributeIn] = Field(default_factory=list)
    variants: list[VariantIn] = Field(
        default_factory=list, description="Variable products only (owner decision Q-32)."
    )

    def to_engine(self) -> engine.ProductIn:
        return engine.ProductIn(
            sku=self.sku,
            kind=self.kind,
            sales_mode=self.sales_mode,
            fiscal_category=self.fiscal_category,
            price_net=self.price_net,
            weight_kg=self.weight_kg,
            translations=tuple(
                engine.TranslationIn(t.language, t.title, t.short_description, t.description)
                for t in self.translations
            ),
            attributes=tuple(
                engine.AttributeIn(a.attribute, tuple(a.values), a.number, a.text, a.variant_axis)
                for a in self.attributes
            ),
            variants=tuple(
                engine.VariantIn(
                    sku=v.sku,
                    options=dict(v.options),
                    price_net_override=v.price_net_override,
                    weight_kg_override=v.weight_kg_override,
                    stock_qty=v.stock_qty,
                    is_default=v.is_default,
                )
                for v in self.variants
            ),
        )


def _vendor(request: HttpRequest, requested: uuid.UUID | None) -> Vendor:
    client = api_client_of(request)
    if requested is not None:
        return get_for_actor_or_404(Vendor, client, public_id=requested)
    if client.vendor is None:
        raise ApiProblem(
            422,
            code="vendor_required",
            detail="A platform key must name the vendor of the product.",
            errors=[
                {"loc": ["body", "payload", "vendor"], "msg": "Field required", "type": "missing"}
            ],
        )
    return client.vendor


@router.post(
    "/products",
    response={200: ChangeSetOut, 201: ChangeSetOut},
    auth=ApiKey(WRITE),
    summary="Create a product with its attributes and variants",
)
def create_product(
    request: HttpRequest, payload: ProductIn, dry_run: DryRun = False
) -> Status[dict[str, Any]]:
    """Create a simple or variable product with its texts, typed attributes, variant axes and
    variants. 201 with the ChangeSet when created; 200 with it on `?dry_run=true`."""
    vendor = _vendor(request, payload.vendor)
    changes = services.create_product(
        vendor, payload.to_engine(), ctx=audit_context(request), dry_run=dry_run
    )
    return Status(200 if dry_run else 201, changeset_out(changes, dry_run=dry_run))


class ProductAttributeOut(Schema):
    code: str
    kind: str
    name: str
    unit: str
    variant_axis: bool
    values: list[ValueOut]
    number: Decimal | None
    text: str


class VariantOut(Schema):
    public_id: uuid.UUID
    sku: str
    position: int
    is_default: bool
    label: str
    options: dict[str, str]
    price_net_override: Decimal | None
    stock_qty: int


class ProductOut(Schema):
    public_id: uuid.UUID
    vendor: uuid.UUID
    sku: str
    kind: str
    status: str
    sales_mode: str
    attributes: list[ProductAttributeOut]
    variant_axes: list[str]
    variants: list[VariantOut]


def _attribute_out(row: ProductAttribute, language: str) -> dict[str, Any]:
    attribute = row.attribute
    return {
        "code": attribute.code,
        "kind": attribute.kind,
        "name": services.attribute_name(attribute, language),
        "unit": attribute.unit,
        "variant_axis": row.is_variant_axis,
        "values": [
            {"value": value.value, "label": services.value_label(value, language)}
            for value in sorted(row.values.all(), key=lambda v: (v.position, v.value))
        ],
        "number": row.numeric_value,
        "text": row.text_value,
    }


def _variant_out(variant: Variant, language: str) -> dict[str, Any]:
    options = services.variant_options(variant)
    return {
        "public_id": variant.public_id,
        "sku": variant.sku,
        "position": variant.position,
        "is_default": variant.is_default,
        "label": engine.variant_label([services.value_label(o.value, language) for o in options]),
        "options": {o.attribute.code: o.value.value for o in options},
        "price_net_override": variant.price_net_override,
        "stock_qty": variant.stock_qty,
    }


@router.get("/products/{public_id}", response=ProductOut, auth=ApiKey(READ), summary="A product")
def get_product(
    request: HttpRequest, public_id: uuid.UUID, query: Annotated[LanguageQuery, Query()]
) -> dict[str, Any]:
    """A product with its typed attributes (names and labels in `language`), its variant axes in
    order and its variants in display order."""
    product = get_for_actor_or_404(
        Product.objects.select_related("vendor"), api_client_of(request), public_id=public_id
    )
    language = query.language
    rows = list(services.product_attributes(product))
    return {
        "public_id": product.public_id,
        "vendor": product.vendor.public_id,
        "sku": product.sku,
        "kind": product.kind,
        "status": product.status,
        "sales_mode": product.sales_mode,
        "attributes": [_attribute_out(row, language) for row in rows],
        "variant_axes": [row.attribute.code for row in rows if row.is_variant_axis],
        "variants": [
            _variant_out(variant, language) for variant in product.variants.order_by("position")
        ],
    }
