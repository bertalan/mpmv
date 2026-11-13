"""ORD.3 / SHP.4: `build_checkout` previews the checkout of a cart (spec 07 §7.2).

The cart is split into one package per vendor. For each package: the lines are checked (for
sale, not on request, in stock), the goods VAT comes from the tax engine, the shipping is quoted by
the shipping engine and its VAT follows the goods (TAX.17), pro rata. The result is a
`CheckoutPreview` with a summary and totals per package, the totals of the ready packages and
blocking messages. It reads only: nothing is held or saved (`place_order` does that).

Tax and shipping never import orders; this module is the one place that joins them.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.cart import engine as cart_engine
from apps.cart import services as cart_services
from apps.cart.models import Cart, CartLine
from apps.catalog.models import Product, Variant
from apps.catalog.services import variant_label
from apps.core import flags, translations
from apps.core.countries import InvalidCountry
from apps.core.money import round_line
from apps.core.services import flag_enabled
from apps.shipping import engine as shipping_engine
from apps.shipping.services import quote_shipping
from apps.tax import engine as tax_engine
from apps.tax import services as tax_services
from apps.tax.models import VatValidation
from apps.vendors import services as vendors_services
from apps.vendors.models import Vendor

from . import engine
from .services import CheckoutPreview, PlaceLine, PlaceVendor, _held

_ZERO = Decimal("0.00")


def _title(product: Product, language: str) -> str:
    shown = translations.resolve(product.translations.all(), language).translation
    return shown.title if shown is not None else product.sku


def _block(code: str, message: str, vendor_id: int | None, **extra: Any) -> engine.Block:
    return engine.Block(code=code, message=message, vendor_id=vendor_id, **extra)


def _line_blocks(
    line: CartLine, vendor: Vendor, *, now: dt.datetime, cart: Cart
) -> list[engine.Block]:
    """Why this line cannot be bought now: on request, not for sale, no price, out of stock."""
    product, variant = line.product, line.variant
    vendor_id = vendor.pk
    # The mode buyers get: a vendor on request (by choice, or without a payout account ready for
    # payouts, D-19) makes its `buy` products on request (VEN.6).
    sales_mode = vendors_services.effective_sales_mode(product)
    if sales_mode == Product.SalesMode.ON_REQUEST:
        return [
            _block(
                engine.ON_REQUEST,
                "This product is available on request only: ask the vendor for a quote.",
                vendor_id,
                product_id=product.pk,
            )
        ]
    error = cart_engine.purchase_error(
        product_status=product.status,
        sales_mode=sales_mode,
        vendor_status=vendor.status,
        is_variable=product.kind == Product.Kind.VARIABLE,
        has_variant=variant is not None,
    )
    if error is not None:
        return [_block(engine.NOT_FOR_SALE, error, vendor_id, product_id=product.pk)]
    stock = variant if variant is not None else product
    limit = cart_engine.available_quantity(
        product.stock_policy, stock.stock_qty, stock.stock_status
    )
    if limit is None:
        return []
    free = max(
        0,
        limit - _held(product.pk, variant.pk if variant else None, now=now, cart=cart),
    )
    if line.quantity > free:
        message = "This item is out of stock." if free == 0 else f"Only {free} available."
        return [_block(engine.OUT_OF_STOCK, message, vendor_id, product_id=product.pk)]
    return []


def _unit_weight(product: Product, variant: Variant | None) -> Decimal:
    if variant is not None and variant.weight_kg_override is not None:
        return variant.weight_kg_override
    return product.weight_kg if product.weight_kg is not None else Decimal("0")


def _options(rates: Sequence[shipping_engine.ShippingRate]) -> tuple[engine.ShippingOption, ...]:
    return tuple(
        engine.ShippingOption(
            key=r.key,
            kind=r.kind,
            label=r.label,
            net=r.net,
            is_default=r.is_default,
            min_days=r.min_days,
            max_days=r.max_days,
        )
        for r in rates
    )


def _choose(
    rates: Sequence[shipping_engine.ShippingRate], key: str | None
) -> shipping_engine.ShippingRate | None:
    """The buyer's rate, else the default (rates come default first)."""
    if key is None:
        return rates[0]
    return next((r for r in rates if r.key == key), None)


def _shipping_choice(
    rate: shipping_engine.ShippingRate, portions: Sequence[tax_engine.TaxPortion]
) -> dict[str, Any]:
    return {
        "key": rate.key,
        "kind": rate.kind,
        "label": rate.label,
        "net": str(rate.net),
        "carrier_code": rate.carrier_code,
        "zone_code": rate.zone_code,
        "service_level": rate.service_level,
        "min_days": rate.min_days,
        "max_days": rate.max_days,
        "free_shipping": rate.free_shipping,
        "tax_portions": [
            {"net": str(p.net), "tax": str(p.tax), "rate": str(p.rate), "reason": str(p.reason)}
            for p in portions
        ],
    }


def _vat_dict(vat: VatValidation | None, ctx: tax_engine.TaxContext) -> dict[str, Any] | None:
    if vat is None or ctx.vat_check is None:
        return None
    return {"at": vat.checked_at.isoformat(), "consultation": vat.request_id, "name": vat.name}


def build_checkout(
    cart: Cart,
    shipping_address: Mapping[str, Any],
    *,
    email: str = "",
    language: str = "en",
    billing_address: Mapping[str, Any] | None = None,
    customer: User | None = None,
    customer_name: str = "",
    customer_vat: VatValidation | None = None,
    shipping_choices: Mapping[int, str] | None = None,
    commission_snapshots: Mapping[int, dict[str, Any]] | None = None,
    quotes_enabled: bool = True,
    now: dt.datetime | None = None,
) -> CheckoutPreview:
    """Preview the checkout of `cart` for the delivery address (`line1`, `city`, `postcode`,
    `country`, optional `state`).

    `commission_snapshots` maps a vendor id to its commission rule snapshot (VEN.2 supplies the
    rule; until then the caller passes it, `place_order` refuses a vendor without one).
    `shipping_choices` maps a vendor id to the key of the rate the buyer chose; without one the
    package takes its default rate. `customer_vat` is the buyer's VIES answer (TAX.5): only a
    valid, fresh one makes a reverse-charge business. Blocking messages are in `preview.blocks`
    and on each package; `preview.placeable` is true when nothing blocks. A package whose
    shipping needs a quote (over the last weight band, special area, no weight, SHP.3) is
    `quote`, not priced, with a `needs_quote` message. Reads only."""
    now = now or timezone.now()
    billing = dict(billing_address) if billing_address else dict(shipping_address)
    delivery = dict(shipping_address)
    blocks: list[engine.Block] = []
    if not flag_enabled(flags.CHECKOUT):
        blocks.append(_block(engine.CHECKOUT_OFF, "Checkout is not available right now.", None))

    cart_packages = cart_services.vendor_packages(cart)
    if not cart_packages:
        blocks.append(_block(engine.EMPTY_CART, "Your cart is empty.", None))

    missing = engine.missing_address_fields(delivery)
    destination: shipping_engine.Destination | None = None
    if not missing:
        try:
            destination = shipping_engine.Destination(
                delivery["country"], str(delivery["postcode"]), str(delivery.get("state") or "")
            )
        except InvalidCountry:
            missing = ["country"]
    if missing:
        blocks.append(
            _block(
                engine.ADDRESS_MISSING,
                "Complete the delivery address: " + ", ".join(missing) + ".",
                None,
                fields=tuple(missing),
            )
        )

    summaries: list[engine.PackageSummary] = []
    ready: list[PlaceVendor] = []
    customer_ctx: tax_engine.TaxContext | None = None
    for package in cart_packages:
        vendor = package.lines[0].item.product.vendor
        summary, place, ctx = _package(
            package,
            vendor,
            cart=cart,
            destination=destination,
            delivery=delivery,
            customer_vat=customer_vat,
            choice=(shipping_choices or {}).get(vendor.pk),
            commission=(commission_snapshots or {}).get(vendor.pk, {}),
            quotes_enabled=quotes_enabled,
            language=language,
            now=now,
        )
        if not flag_enabled(flags.CHECKOUT, vendor=vendor):
            summary = _blocked(
                summary,
                _block(engine.CHECKOUT_OFF, "This vendor cannot take orders right now.", vendor.pk),
            )
            place = None
        summaries.append(summary)
        blocks.extend(summary.blocks)
        if place is not None:
            ready.append(place)
            customer_ctx = customer_ctx or ctx
    totals = (
        engine.add_totals(
            s.totals for s in summaries if s.totals is not None and s.state == engine.READY
        )
        if ready
        else None
    )
    b2b = customer_ctx is not None and customer_ctx.customer != "b2c"
    return CheckoutPreview(
        email=email,
        language=language,
        billing_address=billing,
        billing_country=str(billing.get("country") or ""),
        shipping_address=delivery,
        shipping_country=str(delivery.get("country") or ""),
        vendors=ready,
        cart=cart,
        customer=customer,
        customer_name=customer_name,
        customer_vat_number=customer_vat.vat_number if customer_vat and b2b else "",
        customer_tax_status="b2b" if b2b else "b2c",
        vat_validation=(
            _vat_dict(customer_vat, customer_ctx) if customer_ctx is not None and b2b else None
        ),
        packages=summaries,
        blocks=blocks,
        totals=totals,
    )


def _profile_snapshot(vendor: Vendor, now: dt.datetime) -> dict[str, Any]:
    """The vendor's tax profile valid at `now`, as JSON for the vendor order's snapshot."""
    profile = tax_services.current_tax_profile(vendor, now)
    return {
        "regime": profile.regime,
        "exempt_reason": profile.exempt_reason,
        "exempt_country": profile.exempt_country,
        "exemption_nature": profile.exemption_nature,
        "valid_from": profile.valid_from.isoformat(),
    }


def _blocked(summary: engine.PackageSummary, block: engine.Block) -> engine.PackageSummary:
    return engine.PackageSummary(
        vendor_id=summary.vendor_id,
        state=engine.BLOCKED,
        lines=summary.lines,
        blocks=(*summary.blocks, block),
    )


def _package(
    package: cart_engine.VendorPackage[CartLine],
    vendor: Vendor,
    *,
    cart: Cart,
    destination: shipping_engine.Destination | None,
    delivery: Mapping[str, Any],
    customer_vat: VatValidation | None,
    choice: str | None,
    commission: dict[str, Any],
    quotes_enabled: bool,
    language: str,
    now: dt.datetime,
) -> tuple[engine.PackageSummary, PlaceVendor | None, tax_engine.TaxContext | None]:
    """Check, tax, quote and price one vendor package: (summary, placeable vendor or None, the
    tax context used)."""
    vid = vendor.pk
    cart_lines = [priced.item for priced in package.lines]
    titles = {line.pk: _title(line.product, language) for line in cart_lines}
    bare = tuple(
        engine.SummaryLine(
            product_id=line.product_id,
            variant_id=line.variant_id,
            title=titles[line.pk],
            quantity=line.quantity,
            unit_price_net=priced.unit_price_net if priced.unit_price_net is not None else _ZERO,
        )
        for line, priced in zip(cart_lines, package.lines, strict=True)
    )

    def stop(state: str, *found: engine.Block, **extra: Any) -> tuple[Any, None, None]:
        return (
            engine.PackageSummary(vid, state, bare, blocks=tuple(found), **extra),
            None,
            None,
        )

    found = [b for line in cart_lines for b in _line_blocks(line, vendor, now=now, cart=cart)]
    if found:
        return stop(engine.BLOCKED, *found)
    if destination is None:
        return stop(engine.BLOCKED)  # the cart-level address message says why

    # Goods VAT per line (tax engine). A blocking tax error stops this package only.
    try:
        ctx = tax_services.build_tax_context(
            vendor,
            destination_country=destination.country,
            destination_postcode=destination.postcode or None,
            customer_vat=customer_vat,
            at=now,
        )
        decisions = [
            tax_engine.resolve_goods_tax(ctx, line.product.fiscal_category) for line in cart_lines
        ]
    except (tax_engine.BlockingTaxError, InvalidCountry, LookupError) as error:
        return stop(
            engine.BLOCKED,
            _block(engine.TAX_BLOCKED, f"VAT cannot be calculated for this package: {error}", vid),
        )

    inputs: list[engine.PackageLineInput] = []
    for line, priced, decision in zip(cart_lines, package.lines, decisions, strict=True):
        if priced.unit_price_net is None:  # `_line_blocks` refused unpriced lines
            raise ValueError("a line without a price reached the pricing")
        inputs.append(
            engine.PackageLineInput(priced.unit_price_net, line.quantity, _ZERO, decision)
        )

    # Shipping (SHP.3): one package per vendor, weight from the product or variant.
    ship_package = shipping_engine.VendorPackage(
        vendor_id=vid,
        lines=tuple(
            shipping_engine.PackageLine(
                weight_kg=_unit_weight(line.product, line.variant),
                quantity=line.quantity,
                net_goods=round_line(i.unit_price_net * i.quantity - i.discount_net),
            )
            for line, i in zip(cart_lines, inputs, strict=True)
        ),
    )
    outcome = quote_shipping(
        vendor, ship_package, destination, quotes_enabled=quotes_enabled, language=language
    )
    if isinstance(outcome, shipping_engine.Blocked):
        return stop(engine.BLOCKED, _block(engine.SHIPPING_BLOCKED, outcome.message, vid))
    if isinstance(outcome, (shipping_engine.NeedsQuote, shipping_engine.IndicativeRates)):
        message = (
            outcome.message
            if isinstance(outcome, shipping_engine.NeedsQuote)
            else "Shipping to this area is priced on request."
        )
        options = _options(
            outcome.pickups if isinstance(outcome, shipping_engine.NeedsQuote) else ()
        )
        return stop(
            engine.QUOTE,
            _block(engine.NEEDS_QUOTE, message, vid),
            shipping_options=options,
        )
    rate = _choose(outcome.rates, choice)
    if rate is None:
        return stop(
            engine.BLOCKED,
            _block(engine.SHIPPING_CHOICE_INVALID, "Choose one of the shipping options.", vid),
            shipping_options=_options(outcome.rates),
        )

    amounts = engine.price_package(ctx, inputs, rate.net)
    extra: list[engine.Block] = []
    if len(amounts.shipping_portions) > 1:
        # The order stores one shipping VAT rate per vendor order (database check), so a package
        # with two VAT rates is priced and shown here but cannot be placed until the order keeps
        # the portions (follow-up of ORD.3).
        extra.append(
            _block(
                engine.SHIPPING_VAT_SPLIT,
                "Shipping on a package with different VAT rates cannot be placed yet.",
                vid,
            )
        )
    summary = engine.PackageSummary(
        vendor_id=vid,
        state=engine.BLOCKED if extra else engine.READY,
        lines=tuple(
            engine.SummaryLine(
                product_id=b.product_id,
                variant_id=b.variant_id,
                title=b.title,
                quantity=b.quantity,
                unit_price_net=b.unit_price_net,
                net=p.amounts.net,
                tax_rate=p.decision.rate,
                tax=p.amounts.tax,
            )
            for b, p in zip(bare, amounts.lines, strict=True)
        ),
        blocks=tuple(extra),
        shipping_options=_options(outcome.rates),
        selected_shipping=rate.key,
        shipping_portions=amounts.shipping_portions,
        totals=amounts.totals,
    )
    if extra:
        return summary, None, ctx
    first = amounts.shipping_portions[0] if amounts.shipping_portions else None
    place = PlaceVendor(
        vendor=vendor,
        lines=[
            PlaceLine(
                product=line.product,
                variant=line.variant,
                quantity=line.quantity,
                title=titles[line.pk],
                expected_unit_price_net=i.unit_price_net,
                tax=i.decision,
                variant_label=(
                    variant_label(line.variant, language) if line.variant is not None else ""
                ),
                discount_net=i.discount_net,
            )
            for line, i in zip(cart_lines, inputs, strict=True)
        ],
        shipping_net=rate.net,
        shipping_tax=first,
        shipping_choice=_shipping_choice(rate, amounts.shipping_portions),
        commission_snapshot=commission,
        tax_profile_snapshot=_profile_snapshot(vendor, now),
    )
    return summary, place, ctx
