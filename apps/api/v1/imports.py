"""Import API for external vendors (ING.12): upsert products and set stock, by SKU.

Thin adapters over `apps.catalog.services` (`upsert_products`, `update_by_sku`). Every operation
is scoped to the key's own vendor: a platform key is refused (403 `vendor_key_required`), the
vendor comes from the key only (a row has no vendor field), and a SKU the vendor does not have
answers 404, as for a resource that does not exist. `?dry_run=true` returns the ChangeSet and
writes nothing. Batches are capped at `MAX_ITEMS` rows; the request body size and the per-key
rate limit are the API-wide ones.

Prices are net `Decimal` amounts (EUR, VAT excluded; the tax position stays with the tax app) set
through the audited price services, all or nothing. There is no media operation: API v1 takes no
file upload (tests/integration/test_upload_paths.py); media by URL through the SSRF-guarded client
is a follow-up.

Rows of an upsert are independent (partial apply, no all-or-nothing mode): a refused row is
listed in `errors` and in `rows` (index and SKU) and the others apply.
Stock is all or nothing: one unknown SKU answers 404 and nothing is written.
"""

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from django.db import transaction
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from apps.api.auth import api_client_of
from apps.api.v1.catalog import ProductBody
from apps.api.v1.errors import ApiProblem
from apps.catalog import services
from apps.catalog.engine import MAX_STOCK, SKU_MAX_LENGTH, sku_match_key
from apps.catalog.models import Variant
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


def _refuse_duplicate_skus(skus: list[str]) -> None:
    """One row per SKU (compared by match key): a repeated SKU answers 400 `duplicate_sku`."""
    keys = [sku_match_key(sku) for sku in skus]
    duplicates = sorted({sku for sku, key in zip(skus, keys, strict=True) if keys.count(key) > 1})
    if duplicates:
        raise ApiProblem(
            400,
            code="duplicate_sku",
            detail="A SKU is given more than once in the batch; send one row per SKU.",
            skus=duplicates,
        )


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
    items: list[ProductBody] = Field(min_length=1, max_length=MAX_ITEMS)


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
    variants' own fields); a payload equal to the stored data changes nothing. The vendor
    comes from the key only: rows have no vendor field."""
    vendor = _own_vendor(request)
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
                "outcome": "refused" if index in refused else outcome.get(item.sku, "refused"),
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
    _refuse_duplicate_skus(skus)
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
        # Checked inside the transaction: a SKU renamed or deleted by a concurrent write cannot
        # slip between this check and the write.
        if any(item is None for item in services.match_skus(vendor, skus).values()):
            raise _not_found()
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
    sale_price_net: Decimal | None = Field(
        None,
        description="Products only. A row without any sale field ends the product's current sale, "
        "so send the sale fields again to keep one.",
    )
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
    404; an invalid price answers 422; in both cases nothing is written. A product row with no
    sale field ends the product's current sale (the row states the whole price)."""
    vendor = _own_vendor(request)
    skus = [row.sku for row in payload.items]
    _refuse_duplicate_skus(skus)
    matches = services.match_skus(vendor, skus)
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
