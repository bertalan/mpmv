"""Apply the stock and price plans of a supplier source (ING.8).

The pure rules live in `engine.plan_stock_actions` / `plan_price_actions`; this module loads the
rows, calls them, writes through the audited `catalog.services` and opens a `ReviewItem` for what a
person must decide (stale data, a price jump, a sale). Nothing is written on a dry run or when the
run itself is a dry run (the per-source write flag, spec 08). Ingest never imports `apps.tax`
(spec 01 graph): the VAT rate of a supplier gross price comes from a resolver named by the
`INGEST_VAT_RATE_RESOLVER` setting.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.module_loading import import_string

from apps.catalog import services as catalog_services
from apps.catalog.models import Product, StockStatus
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.services import run_mutation

from . import engine
from .models import ImportRun, ReviewItem, SourceItem
from .services import REVIEW_OPENED, review_snapshot

logger = logging.getLogger(__name__)

SYNC_RUN = "ingest.sync.run"

type VatRateFor = Callable[[Product, dt.datetime], Decimal]
"""The VAT rate (a fraction, 0.22 for 22 percent) of a product's supplier gross price at a time."""


@dataclass
class SyncResult:
    actions: list[engine.StockAction | engine.PriceAction]
    changes: ChangeSet
    applied: int = 0
    reviews: int = 0
    errors: list[str] = field(default_factory=list)


def stale_max_age() -> dt.timedelta:
    """Provisional: how old an observation may be (`INGEST_STALE_MAX_AGE_HOURS`, pending the
    lane-a or owner decision). The services take the age as a required argument."""
    return dt.timedelta(hours=settings.INGEST_STALE_MAX_AGE_HOURS)


def vat_rate_resolver() -> VatRateFor:
    resolver: VatRateFor = import_string(settings.INGEST_VAT_RATE_RESOLVER)
    return resolver


def _items(run: ImportRun) -> list[SourceItem]:
    """The source's matched items with their products locked: the plan is made from the rows that
    are then written (call inside a transaction)."""
    return list(
        SourceItem.objects.filter(source=run.source, product__isnull=False)
        .select_related("product", "state", "missing")
        .select_for_update(of=("product",))
        .order_by("pk")
    )


def _product(item: SourceItem) -> Product:
    if item.product is None:  # _items only loads linked items
        raise ValueError("source item without a product")
    return item.product


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def _open_review(
    run: ImportRun,
    item: SourceItem,
    kind: str,
    payload: dict[str, Any],
    *,
    fingerprint: str,
    ctx: AuditContext,
    changes: ChangeSet,
    dry_run: bool,
) -> bool:
    """Open a non-blocking review item unless this item already has one of this kind for the same
    observation, in any decision: a person's rejection is not asked again until the supplier says
    something new (`fingerprint` is the observed value)."""
    if ReviewItem.objects.filter(item=item, kind=kind, payload__fingerprint=fingerprint).exists():
        return False
    review = ReviewItem(
        run=run,
        item=item,
        product=item.product,
        kind=kind,
        blocking=engine.is_blocking(kind),
        payload={**payload, "fingerprint": fingerprint},
    )
    review.full_clean()
    part = ChangeSet()
    part.create("ingest.reviewitem", str(review.public_id), review_snapshot(review))
    run_mutation(part, review.save, ctx=ctx, action=REVIEW_OPENED, dry_run=dry_run)
    changes.changes.extend(part.changes)
    return True


def _summary(
    run: ImportRun, result: SyncResult, kind: str, dry_run: bool, ctx: AuditContext
) -> None:
    record(
        ctx,
        SYNC_RUN,
        "ingest.importrun",
        str(run.public_id),
        None,
        {
            "kind": kind,
            "dry_run": dry_run,
            "actions": len(result.actions),
            "applied": result.applied,
            "reviews": result.reviews,
            "errors": len(result.errors),
        },
    )


def sync_stock(
    run: ImportRun,
    *,
    ctx: AuditContext,
    now: dt.datetime,
    max_age: dt.timedelta,
    dry_run: bool = False,
) -> SyncResult:
    """Plan and apply the stock of the source's matched items.

    The supplier is authoritative: exact quantity, a 404 sets 0 (the page stays published), stale
    data is flagged and never applied. Stock is written all at once through
    `catalog.services.update_by_sku`.
    """
    dry = dry_run or run.dry_run
    with transaction.atomic():
        items = {item.pk: item for item in _items(run)}
        observations: list[engine.StockObservation] = []
        for item in items.values():
            missing = getattr(item, "missing", None)
            state = getattr(item, "state", None)
            if missing is not None:
                observations.append(
                    engine.StockObservation(
                        item.pk,
                        item.product_id,
                        missing.last_seen_at,
                        missing_status=missing.http_status,
                    )
                )
            elif state is not None:
                observations.append(
                    engine.StockObservation(
                        item.pk,
                        item.product_id,
                        state.observed_at,
                        state.stock_status,
                        state.stock_quantity,
                    )
                )
        observed = {o.ref: o.observed_at.isoformat() for o in observations}
        products = {
            _product(item).pk: engine.StockProduct(_product(item).pk, _product(item).stock_qty)
            for item in items.values()
        }
        plan = engine.plan_stock_actions(observations, products, now=now, max_age=max_age)
        result = SyncResult(list(plan), ChangeSet())
        rows = []
        for action in plan:
            item = items[action.ref]
            if action.kind == engine.SET_STOCK and action.quantity is not None:
                status = StockStatus.IN_STOCK if action.quantity > 0 else StockStatus.OUT_OF_STOCK
                rows.append(
                    catalog_services.SkuRow(
                        _product(item).sku,
                        {"stock_qty": action.quantity, "stock_status": status},
                    )
                )
            elif action.kind == engine.REVIEW and action.review_kind:
                payload = {
                    "sync": "stock",
                    "reason": action.reason,
                    "observed_at": observed[action.ref],
                }
                if _open_review(
                    run,
                    item,
                    action.review_kind,
                    payload,
                    fingerprint=observed[action.ref],
                    ctx=ctx,
                    changes=result.changes,
                    dry_run=dry,
                ):
                    result.reviews += 1
        if rows:
            part = catalog_services.update_by_sku(run.source.vendor, rows, ctx=ctx, dry_run=dry)
            result.changes.changes.extend(part.changes)
            result.applied = 0 if dry else len(rows)
        if not dry:
            _summary(run, result, "stock", dry, ctx)
    return result


def sync_prices(
    run: ImportRun,
    *,
    ctx: AuditContext,
    now: dt.datetime,
    max_age: dt.timedelta,
    vat_rate_for: VatRateFor,
    max_change_pct: Decimal = engine.DEFAULT_MAX_CHANGE_PCT,
    dry_run: bool = False,
) -> SyncResult:
    """Plan and apply the net prices of the source's matched items (supplier gross prices from the
    item's state: `price` and an optional `sale_price`).

    `vat_rate_for(product, observed_at)` gives the rate (looked up once per fiscal category, vendor
    and day); a product without one is an error, never a guess. A VAT-exempt vendor's rate is 0, so
    its net price equals the supplier gross (pending a lane-a decision, pinned by a test). A
    change above `max_change_pct`, a sale and stale data open a `ReviewItem` and write nothing;
    the product's own sale is kept when the regular price moves.
    """
    dry = dry_run or run.dry_run
    with transaction.atomic():
        items = {item.pk: item for item in _items(run) if getattr(item, "state", None) is not None}
        order = {pk: n for n, pk in enumerate(items)}
        result = SyncResult([], ChangeSet())
        by_rate: dict[
            Decimal, tuple[list[engine.PriceObservation], dict[int, engine.PriceProduct]]
        ] = {}
        rates: dict[tuple[str, int, dt.date], Decimal | str] = {}
        obs_by_ref: dict[int, engine.PriceObservation] = {}
        for item in items.values():
            state = item.state
            product = _product(item)
            key = (product.fiscal_category, product.vendor_id, state.observed_at.date())
            if key not in rates:  # one tax lookup per category, vendor and day, not per product
                try:
                    rates[key] = vat_rate_for(product, state.observed_at)
                except Exception as exc:  # no rate or profile: reported, the rest goes on
                    logger.warning(
                        "ingest price sync: no VAT rate for %s", product.sku, exc_info=True
                    )
                    rates[key] = str(exc)
            rate = rates[key]
            if isinstance(rate, str):
                result.errors.append(f"{product.sku}: {rate}")
                continue
            observation = engine.PriceObservation(
                item.pk,
                product.pk,
                state.observed_at,
                _decimal(state.state.get("price")),
                _decimal(state.state.get("sale_price")),
            )
            obs_by_ref[item.pk] = observation
            observations, products = by_rate.setdefault(rate, ([], {}))
            observations.append(observation)
            products[product.pk] = engine.PriceProduct(product.pk, product.price_net)
        price_actions: list[engine.PriceAction] = []
        for rate, (observations, products) in by_rate.items():
            price_actions.extend(
                engine.plan_price_actions(
                    observations,
                    products,
                    now=now,
                    max_age=max_age,
                    vat_rate=rate,
                    max_change_pct=max_change_pct,
                )
            )
        price_actions.sort(key=lambda action: order[action.ref])  # item order, whatever the rate
        result.actions.extend(price_actions)
        for action in price_actions:
            item = items[action.ref]
            if action.kind == engine.SET_PRICE and action.price_net is not None:
                _set_price(_product(item), action.price_net, ctx=ctx, dry=dry, result=result)
            elif action.kind == engine.REVIEW and action.review_kind:
                observation = obs_by_ref[action.ref]
                fingerprint = (
                    observation.observed_at.isoformat()
                    if action.reason == "stale"
                    else f"{observation.price_gross}|{observation.sale_price_gross}"
                )
                payload = {
                    "sync": "price",
                    "reason": action.reason,
                    "old_net": _text(action.old_net),
                    "new_net": _text(action.price_net),
                    "change_pct": _text(action.change_pct),
                }
                if _open_review(
                    run,
                    item,
                    action.review_kind,
                    payload,
                    fingerprint=fingerprint,
                    ctx=ctx,
                    changes=result.changes,
                    dry_run=dry,
                ):
                    result.reviews += 1
        if not dry:
            _summary(run, result, "price", dry, ctx)
    return result


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _set_price(
    product: Product, price_net: Decimal, *, ctx: AuditContext, dry: bool, result: SyncResult
) -> None:
    try:
        with transaction.atomic():
            part = catalog_services.set_product_price(
                product,
                price_net=price_net,
                sale_price_net=product.sale_price_net,
                sale_from=product.sale_from,
                sale_to=product.sale_to,
                ctx=ctx,
                dry_run=dry,
            )
    except ValidationError as exc:
        result.errors.append(f"{product.sku}: {'; '.join(exc.messages)}")
        return
    result.changes.changes.extend(part.changes)
    if not dry:
        result.applied += 1
