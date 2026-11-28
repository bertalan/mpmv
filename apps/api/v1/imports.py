"""Import API for external vendors (ING.12): upsert products and set stock, by SKU.

Thin adapters over `apps.catalog.services` (`upsert_products`, `update_by_sku`). Every operation
is scoped to the key's own vendor: a platform key is refused (403 `vendor_key_required`), a row
naming another vendor or a SKU the vendor does not have answers 404, as for a resource that does
not exist. `?dry_run=true` returns the ChangeSet and writes nothing. Batches are capped at
`MAX_ITEMS` rows; the request body size and the per-key rate limit are the API-wide ones.

Prices are net `Decimal` amounts (EUR, VAT excluded; the tax position stays with the tax app) set
through the audited price services, all or nothing. Media is a multipart upload attached through
`catalog.media.upload_image` (licence and author required); fetching a URL is a follow-up that
needs the SSRF-guarded client.

Rows of an upsert are independent (partial apply, no all-or-nothing mode): a refused row is
listed in `errors` and in `rows` (index and SKU) and the others apply.
Stock is all or nothing: one unknown SKU answers 404 and nothing is written.
"""

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from ninja import File, Form, Router, Schema, Status, UploadedFile
from pydantic import Field

from apps.api.auth import api_client_of
from apps.api.v1.catalog import ProductIn
from apps.api.v1.errors import ApiProblem
from apps.catalog import media, services
from apps.catalog.engine import MAX_STOCK, SKU_MAX_LENGTH
from apps.catalog.models import Variant
from apps.core.changeset import ChangeSet
from apps.core.models import ImageLicence
from apps.vendors.models import Vendor
from apps.vendors.services import ensure_vendor_can_write

from .mutations import ChangeSetOut, DryRun, audit_context, changeset_out
from .security import ApiKey
from .text import PlainText

router = Router(tags=["import"])

MAX_ITEMS = 500


def _own_vendor(request: HttpRequest) -> Vendor:
    vendor = api_client_of(request).vendor
    if vendor is None:
        raise ApiProblem(
            403,
            code="vendor_key_required",
            detail="The import API is for a vendor's own key; a platform key is refused.",
        )
    return vendor


def _vendor_key(request: HttpRequest) -> media.VendorKey:
    client = api_client_of(request)
    return media.VendorKey(
        vendor_id=client.vendor_id,
        scopes=frozenset(client.scopes),
        active=client.is_active
        and client.revoked_at is None
        and client.expires_at > timezone.now(),
    )


def _not_found() -> ApiProblem:
    return ApiProblem(404, code="not_found", detail="The resource does not exist.")


class RowErrorOut(Schema):
    index: int
    sku: str
    errors: dict[str, list[str]]


class RowOutcomeOut(Schema):
    index: int = Field(description="Position of the row in `items`, from 0.")
    sku: str
    outcome: Literal["created", "updated", "unchanged", "refused"]


class UpsertOut(ChangeSetOut):
    rows: list[RowOutcomeOut] = Field(
        description="One entry per submitted row, in order: which rows applied (created, updated, "
        "unchanged) and which were refused (see `errors` for why). A refused row writes nothing; "
        "the others apply (partial apply)."
    )
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    errors: list[RowErrorOut]


class UpsertIn(Schema):
    items: list[ProductIn] = Field(min_length=1, max_length=MAX_ITEMS)


@router.post(
    "/products",
    response=UpsertOut,
    auth=ApiKey("catalog:write"),
    summary="Upsert products by SKU",
)
def upsert_products(
    request: HttpRequest, payload: UpsertIn, dry_run: DryRun = False
) -> dict[str, Any]:
    """Create new SKUs as drafts and update known ones (fields, net price, texts per language,
    variants' own fields); a payload equal to the stored data changes nothing. A row naming
    another vendor answers 404 for the whole request."""
    vendor = _own_vendor(request)
    if any(item.vendor not in (None, vendor.public_id) for item in payload.items):
        raise _not_found()
    report = services.upsert_products(
        vendor,
        [item.to_engine() for item in payload.items],
        ctx=audit_context(request),
        dry_run=dry_run,
        require_active_vendor=True,
    )
    outcome = (
        {sku: "created" for sku in report.created}
        | {sku: "updated" for sku in report.updated}
        | {sku: "unchanged" for sku in report.unchanged}
    )
    refused = {e.index for e in report.errors}
    return {
        **changeset_out(report.changes, dry_run=dry_run),
        "rows": [
            {
                "index": index,
                "sku": item.sku,
                "outcome": "refused" if index in refused else outcome[item.sku],
            }
            for index, item in enumerate(payload.items)
        ],
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "errors": [{"index": e.index, "sku": e.sku, "errors": e.errors} for e in report.errors],
    }


class StockRowIn(Schema):
    sku: PlainText = Field(min_length=1, max_length=SKU_MAX_LENGTH)
    stock_qty: int = Field(ge=0, le=MAX_STOCK)
    stock_status: Literal["in_stock", "out_of_stock", "on_backorder"] | None = Field(
        None, description="Left as stored when absent."
    )


class StockIn(Schema):
    items: list[StockRowIn] = Field(min_length=1, max_length=MAX_ITEMS)


@router.put(
    "/stock",
    response=ChangeSetOut,
    auth=ApiKey("stock:write"),
    summary="Set stock by SKU",
)
def set_stock(request: HttpRequest, payload: StockIn, dry_run: DryRun = False) -> dict[str, Any]:
    """Set the exact quantity (and optionally the status) of products and variants by SKU. All
    or nothing: a SKU the vendor does not have, another vendor's included, answers 404."""
    vendor = _own_vendor(request)
    skus = [row.sku for row in payload.items]
    if any(item is None for item in services.match_skus(vendor, skus).values()):
        raise _not_found()
    rows = [
        services.SkuRow(
            row.sku,
            {"stock_qty": row.stock_qty}
            | ({"stock_status": row.stock_status} if row.stock_status else {}),
        )
        for row in payload.items
    ]
    with transaction.atomic():
        # A vendor suspended since the key was authenticated writes nothing (CORE.14).
        ensure_vendor_can_write(vendor)
        changes: ChangeSet = services.update_by_sku(
            vendor, rows, ctx=audit_context(request), dry_run=dry_run
        )
    return changeset_out(changes, dry_run=dry_run)


class PriceRowIn(Schema):
    sku: PlainText = Field(min_length=1, max_length=SKU_MAX_LENGTH)
    price_net: Decimal | None = Field(
        description="Net price, EUR, 2 decimals, not negative; for a variant, its own override "
        "(null clears it). Null on a product is allowed only when it is not for sale."
    )
    sale_price_net: Decimal | None = Field(None, description="Products only.")
    sale_from: dt.datetime | None = Field(None, description="Products only.")
    sale_to: dt.datetime | None = Field(None, description="Products only.")


class PricesIn(Schema):
    items: list[PriceRowIn] = Field(min_length=1, max_length=MAX_ITEMS)


@router.put(
    "/prices",
    response=ChangeSetOut,
    auth=ApiKey("catalog:write"),
    summary="Set net prices by SKU",
)
def set_prices(request: HttpRequest, payload: PricesIn, dry_run: DryRun = False) -> dict[str, Any]:
    """Set the net price (and the optional sale) of products, or the price override of variants,
    by SKU. All or nothing: a SKU the vendor does not have, another vendor's included, answers
    404; an invalid price answers 422; in both cases nothing is written."""
    vendor = _own_vendor(request)
    matches = services.match_skus(vendor, [row.sku for row in payload.items])
    if any(item is None for item in matches.values()):
        raise _not_found()
    ctx = audit_context(request)
    changes = ChangeSet()
    with transaction.atomic():
        ensure_vendor_can_write(vendor)
        for row in payload.items:
            item = matches[row.sku]
            if item is None:  # pragma: no cover - refused above
                raise _not_found()
            if isinstance(item, Variant):
                if row.sale_price_net is not None or row.sale_from or row.sale_to:
                    raise ApiProblem(
                        422,
                        code="validation_error",
                        detail="A variant has no sale of its own.",
                        errors=[{"loc": ["body", "items"], "msg": row.sku, "type": "invalid"}],
                    )
                done = services.set_variant_price(
                    item, price_net_override=row.price_net, ctx=ctx, dry_run=dry_run
                )
            else:
                done = services.set_product_price(
                    item,
                    price_net=row.price_net,
                    sale_price_net=row.sale_price_net,
                    sale_from=row.sale_from,
                    sale_to=row.sale_to,
                    ctx=ctx,
                    dry_run=dry_run,
                )
            changes.changes.extend(done.changes)
    return changeset_out(changes, dry_run=dry_run)


class MediaForm(Schema):
    sku: PlainText = Field(min_length=1, max_length=SKU_MAX_LENGTH)
    title: PlainText = Field(min_length=1, max_length=255)
    author: PlainText = Field(min_length=1, max_length=255)
    licence: PlainText = Field(description="Code of an entry of the image licence allow list.")
    language: PlainText | None = Field(None, description="Dedicated to this language; or all.")
    alt_text: PlainText = Field("", max_length=255)


@router.post(
    "/media",
    response={200: ChangeSetOut, 201: ChangeSetOut},
    auth=ApiKey("catalog:write"),
    summary="Attach an uploaded image to a product or variant by SKU",
)
def attach_media(
    request: HttpRequest,
    data: Form[MediaForm],
    file: File[UploadedFile],
    dry_run: DryRun = False,
) -> Status[dict[str, Any]]:
    """Add an image (multipart `file`) to the gallery of the product or variant with this SKU.
    The file is checked like every image upload (type, size, pixels). 201 when added; 200 with
    the ChangeSet on `?dry_run=true`."""
    vendor = _own_vendor(request)
    item = services.match_skus(vendor, [data.sku])[data.sku]
    if item is None:
        raise _not_found()
    licence = ImageLicence.objects.filter(code=data.licence).first()
    if licence is None:
        raise ApiProblem(
            422,
            code="validation_error",
            detail="The request is not valid.",
            errors=[{"loc": ["body", "licence"], "msg": "Unknown licence.", "type": "invalid"}],
        )
    product, variant = (item.product, item) if isinstance(item, Variant) else (item, None)
    changes = media.upload_image(
        product,
        file=file,
        title=data.title,
        licence=licence,
        author=data.author,
        ctx=audit_context(request),
        variant=variant,
        language=data.language,
        alt_text=data.alt_text,
        dry_run=dry_run,
        key=_vendor_key(request),
    )
    return Status(200 if dry_run else 201, changeset_out(changes, dry_run=dry_run))
