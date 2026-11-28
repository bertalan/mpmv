"""Import API for external vendors (ING.12): upsert products and set stock, by SKU.

Thin adapters over `apps.catalog.services` (`upsert_products`, `update_by_sku`). Every operation
is scoped to the key's own vendor: a platform key is refused (403 `vendor_key_required`), a row
naming another vendor or a SKU the vendor does not have answers 404, as for a resource that does
not exist. `?dry_run=true` returns the ChangeSet and writes nothing. Batches are capped at
`MAX_ITEMS` rows; the request body size and the per-key rate limit are the API-wide ones.

Rows of an upsert are independent: a refused row is listed in `errors` and the others apply.
Stock is all or nothing: one unknown SKU answers 404 and nothing is written.
"""

from typing import Any, Literal

from django.db import transaction
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from apps.api.auth import api_client_of
from apps.api.v1.catalog import ProductIn
from apps.api.v1.errors import ApiProblem
from apps.catalog import services
from apps.catalog.engine import MAX_STOCK, SKU_MAX_LENGTH
from apps.core.changeset import ChangeSet
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


def _not_found() -> ApiProblem:
    return ApiProblem(404, code="not_found", detail="The resource does not exist.")


class RowErrorOut(Schema):
    index: int
    sku: str
    errors: dict[str, list[str]]


class UpsertOut(ChangeSetOut):
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
    return {
        **changeset_out(report.changes, dry_run=dry_run),
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
