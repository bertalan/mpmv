"""Cart and checkout pages (ORD.5): thin adapters over the cart services, `build_checkout` and
`place_order`.

Steps: cart, details (address, and for a business the company and VAT id, checked with VIES),
review (shipping per vendor package, totals, "place order"), then the confirmation. A page is
placeable only when `build_checkout` says so; the buttons never offer what the preview refuses.
Payment is not here (PAY.1): the confirmation shows a placeholder.

Protection of the public POSTs: CSRF (middleware); the details and the order placement are
behind Turnstile and a per-IP budget (the VIES lookup and the stock hold are the costly parts);
the cart and shipping updates have a per-IP budget and write nothing but the visitor's own
cart or session. Pages are never cached or indexed.
"""

import datetime as dt
import math
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.captcha import captcha_passes
from apps.accounts.models import User
from apps.cart import services as cart_services
from apps.cart.models import Cart
from apps.cart.session import current_cart
from apps.core.audit import audit_context
from apps.core.http import client_ip
from apps.core.privacy import keyed_hash
from apps.core.public_forms import Protection, public_form
from apps.core.services import consume_rate_limit
from apps.orders import engine
from apps.orders.checkout import build_checkout
from apps.orders.models import Order
from apps.orders.services import CheckoutClosed, CheckoutPreview, OrderRefused, place_order
from apps.tax import engine as tax_engine
from apps.tax import services as tax_services
from apps.tax.models import VatValidation
from apps.vendors.models import Vendor

from . import present, state
from .forms import MAX_BODY_BYTES, CheckoutDetailsForm, vat_country

RATE_WINDOW = dt.timedelta(hours=1)
CART_LIMIT = 600  # cart and shipping updates per address and hour
DETAILS_LIMIT = 40  # details submissions (each may ask VIES) per address and hour
PLACE_LIMIT = 20  # order placements (each holds stock) per address and hour


def _private(response: HttpResponse) -> HttpResponse:
    response["Referrer-Policy"] = "strict-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    add_never_cache_headers(response)
    return response


def _page(
    request: HttpRequest, template: str, context: dict[str, Any], status: int = 200
) -> HttpResponse:
    return _private(render(request, template, context, status=status))


def _too_many(wait: dt.timedelta) -> HttpResponse:
    seconds = str(max(1, math.ceil(wait.total_seconds())))
    response = HttpResponse(
        _("Too many requests from this address. Try again later."),
        status=429,
        headers={"Retry-After": seconds},
    )
    return _private(response)


def _budget(request: HttpRequest, name: str, limit: int) -> dt.timedelta:
    ip = keyed_hash(client_ip(request) or "unknown", purpose="checkout-ip")
    return consume_rate_limit(f"checkout:{name}:{ip}", limit=limit, window=RATE_WINDOW)


def _htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _steps(current: int) -> dict[str, Any]:
    return {
        "steps": [
            {"label": _("Cart"), "url": reverse("orders:cart")},
            {"label": _("Details"), "url": reverse("orders:checkout")},
            {"label": _("Review and pay"), "url": reverse("orders:review")},
        ],
        "current_step": current,
    }


def _vendors(cart: Cart) -> dict[int, Vendor]:
    return {
        line.product.vendor_id: line.product.vendor
        for line in cart.lines.select_related("product__vendor")
    }


def _customer(request: HttpRequest) -> User | None:
    user = request.user
    return user if user.is_authenticated and isinstance(user, User) else None


def _preview(request: HttpRequest, cart: Cart, data: dict[str, Any]) -> CheckoutPreview:
    address = data.get("address")
    vendors = _vendors(cart)
    validation = None
    # D-28: the answer backs the reverse charge only for the vendors it was checked as.
    validation_pk = state.vat_validation_for(data, vendors) if data.get("business") else None
    if validation_pk is not None:
        validation = VatValidation.objects.filter(pk=validation_pk).first()
    snapshots = state.commission_snapshots(vendors)
    return build_checkout(
        cart,
        address if isinstance(address, dict) else {},
        email=str(data.get("email") or ""),
        language=get_language() or "en",
        customer=_customer(request),
        customer_name=str((address or {}).get("name") or "") if isinstance(address, dict) else "",
        customer_vat=validation,
        shipping_choices=state.shipping_choices(data),
        commission_snapshots=snapshots,
    )


def _ordering_open(preview: CheckoutPreview) -> bool:
    """False while a ready vendor has no commission rule (VEN.2): no order is placed then."""
    return all(item.commission_snapshot for item in preview.vendors)


# --- Cart ------------------------------------------------------------------------------------


def _cart_context(request: HttpRequest, cart: Cart | None, status: str = "") -> dict[str, Any]:
    context: dict[str, Any] = {"status": status, "empty": True, **_steps(1)}
    if cart is None or not cart.lines.exists():
        return context
    preview = _preview(request, cart, state.load(request))
    packages = present.package_views(preview, cart, _vendors(cart))
    cart_messages = present.cart_blocks(preview, hide=(engine.ADDRESS_MISSING,))
    context.update(
        empty=False,
        packages=packages,
        cart_messages=cart_messages,
        address_known=not any(b.code == engine.ADDRESS_MISSING for b in preview.blocks),
        order=present.order_totals(preview),
        can_continue=not cart_messages
        and not any(
            p.messages or p.quote_url or any(ln.problem for ln in p.lines) for p in packages
        ),
    )
    return context


def _apply_cart_changes(request: HttpRequest, cart: Cart) -> list[str]:
    """Apply `remove` and the `qty_<id>` fields; the problems found, in words."""
    problems: list[str] = []
    lines = {str(line.public_id): line for line in cart.lines.select_related("product")}
    removed = request.POST.get("remove", "")
    if removed in lines:
        cart_services.remove_line(lines.pop(removed))
        return problems
    for public_id, line in lines.items():
        raw = request.POST.get(f"qty_{public_id}")
        if raw is None:
            continue
        try:
            quantity = int(raw)
        except ValueError:
            problems.append(_("Enter a whole number as the quantity."))
            continue
        if quantity == line.quantity:
            continue
        try:
            cart_services.set_line_quantity(line, quantity)
        except ValidationError as error:
            problems.extend(error.messages)
    return problems


@public_form(
    protection=Protection.ALLOW,
    purpose="Cart: change a quantity or remove a line",
    task="ORD.5",
    reason=(
        "it changes only the visitor's own guest or user cart, found from the session, and "
        "calls nothing external"
    ),
    rate_limit="per IP (cart budget)",
)
@require_http_methods(["GET", "POST"])
def cart_page(request: HttpRequest) -> HttpResponse:
    cart = current_cart(request)
    if request.method == "POST":
        if cart is None:
            return redirect("orders:cart")
        wait = _budget(request, "cart", CART_LIMIT)
        if wait:
            return _too_many(wait)
        problems = _apply_cart_changes(request, cart)
        if not _htmx(request):
            for problem in problems:
                messages.warning(request, problem)
            return _private(HttpResponseRedirect(reverse("orders:cart")))
        status = " ".join(problems) or _("Cart updated.")
        context = {**_cart_context(request, cart, status), "partial": True}
        return _page(request, "orders/_cart_body.html", context)
    return _page(request, "orders/cart.html", _cart_context(request, cart))


# --- Details (address, business, VIES) -------------------------------------------------------


def _verify_business(
    cart: Cart, vat_id: str, country: str
) -> tuple[VatValidation | None, list[int], str]:
    """Ask VIES, as every vendor of the cart (D-28), and give back the answer that backs the
    reverse charge with the ids of the vendors it covers, or None with the reason in words when
    any vendor lacks the proof."""
    vendors = list(_vendors(cart).values())
    checks = [
        tax_services.verify_vat_number_for_order(country, vat_id, vendor=vendor)
        for vendor in vendors
    ]
    if checks and all(check.verified and check.validation is not None for check in checks):
        return (
            checks[0].validation,
            [vendor.pk for vendor in vendors],
            _("Your VAT number is valid. VAT is not charged where the reverse charge applies."),
        )
    reasons = {check.reason for check in checks if not check.verified}
    if tax_engine.VAT_REASON_INVALID in reasons:
        return (
            None,
            [],
            _("VIES says this VAT number is not valid. VAT is charged on your order."),
        )
    return (
        None,
        [],
        _("We could not confirm your VAT number with VIES now, so VAT is charged on your order."),
    )


def _initial(request: HttpRequest, data: dict[str, Any]) -> dict[str, Any]:
    stored = data.get("address")
    address: dict[str, Any] = stored if isinstance(stored, dict) else {}
    user = _customer(request)
    return {
        "email": data.get("email") or (user.email if user else ""),
        "full_name": address.get("name", ""),
        "line1": address.get("line1", ""),
        "line2": address.get("line2", ""),
        "postcode": address.get("postcode", ""),
        "city": address.get("city", ""),
        "state": address.get("state", ""),
        "country": address.get("country", ""),
        "is_business": bool(data.get("business")),
        "company": address.get("company", ""),
        "vat_id": data.get("vat_id", ""),
    }


def _details_page(
    request: HttpRequest, form: CheckoutDetailsForm, status: int = 200, **extra: Any
) -> HttpResponse:
    return _page(
        request, "orders/checkout.html", {"form": form, **_steps(2), **extra}, status=status
    )


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Checkout details: delivery address and business VAT number",
    task="ORD.5",
    rate_limit="per IP (checkout budget)",
    templates=("orders/checkout.html",),
)
@require_http_methods(["GET", "POST"])
def checkout_page(request: HttpRequest) -> HttpResponse:
    cart = current_cart(request)
    if cart is None or not cart.lines.exists():
        return redirect("orders:cart")
    data = state.load(request)
    if request.method == "GET":
        return _details_page(request, CheckoutDetailsForm(initial=_initial(request, data)))
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = MAX_BODY_BYTES + 1
    if length > MAX_BODY_BYTES:
        return _private(HttpResponse(_("The request is too large."), status=413))
    form = CheckoutDetailsForm(request.POST)
    if not captcha_passes(request):
        form.add_error(None, _("The verification failed. Please reload the page and try again."))
        return _details_page(request, form)
    wait = _budget(request, "details", DETAILS_LIMIT)
    if wait:
        return _too_many(wait)
    if not form.is_valid():
        return _details_page(request, form)
    cleaned = form.cleaned_data
    validation: VatValidation | None = None
    covered: list[int] = []
    note = ""
    if cleaned["is_business"]:
        validation, covered, note = _verify_business(
            cart, cleaned["vat_id"], vat_country(cleaned["vat_id"], cleaned["country"])
        )
    state.save(
        request,
        email=cleaned["email"],
        address=form.address(),
        business=bool(cleaned["is_business"]),
        vat_id=cleaned["vat_id"],
        vat_validation=validation.pk if validation is not None else None,
        vat_vendors=covered,
        vat_note=str(note),
        vat_ok=validation is not None,
        shipping={},
    )
    return _private(HttpResponseRedirect(reverse("orders:review")))


# --- Review ----------------------------------------------------------------------------------


def _review_context(
    request: HttpRequest, cart: Cart, data: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    preview = _preview(request, cart, data)
    vendors = _vendors(cart)
    packages = present.package_views(preview, cart, vendors)
    open_ = _ordering_open(preview)
    vat_ok = bool(data.get("vat_ok"))
    vat_note = data.get("vat_note", "")
    if vat_ok and state.vat_validation_for(data, vendors) is None:
        vat_ok = False
        vat_note = _(
            "Your cart changed after we checked your VAT number, so VAT is charged on your "
            "order. Confirm your details again to check it for every seller."
        )
    return {
        **_steps(3),
        "packages": packages,
        "cart_messages": present.cart_blocks(preview),
        "order": present.order_totals(preview),
        "placeable": preview.placeable and open_,
        "ordering_closed": preview.placeable and not open_,
        "address": data.get("address") or {},
        "email": data.get("email", ""),
        "vat_note": vat_note,
        "vat_ok": vat_ok,
        "business": bool(data.get("business")),
        "preview": preview,
        **extra,
    }


def _save_shipping(request: HttpRequest, cart: Cart, data: dict[str, Any]) -> dict[str, Any]:
    chosen = dict(data.get("shipping") or {})
    for vendor in _vendors(cart).values():
        key = request.POST.get(f"ship_{vendor.public_id}")
        if key:
            chosen[str(vendor.pk)] = key
    return state.save(request, shipping=chosen)


def _refusal_messages(refused: OrderRefused) -> list[str]:
    texts = {
        "price_changed": _("A price changed while you were checking out. Review the new prices."),
        "unavailable": _("An item is no longer available."),
        "out_of_stock": _("An item went out of stock."),
    }
    seen: list[str] = []
    for reason in refused.reasons:
        message = texts.get(reason.code, _("The order could not be placed."))
        if message not in seen:
            seen.append(message)
    return seen


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Checkout review: place the order (holds stock)",
    task="ORD.5",
    rate_limit="per IP (checkout budget)",
    templates=("orders/review.html",),
)
@require_http_methods(["GET", "POST"])
def review_page(request: HttpRequest) -> HttpResponse:
    cart = current_cart(request)
    data = state.load(request)
    if cart is None or not cart.lines.exists():
        return redirect("orders:cart")
    if not data.get("address"):
        return redirect("orders:checkout")
    if request.method == "GET":
        return _page(request, "orders/review.html", _review_context(request, cart, data))
    data = _save_shipping(request, cart, data)
    if not captcha_passes(request):
        context = _review_context(
            request,
            cart,
            data,
            errors=[_("The verification failed. Please reload the page and try again.")],
        )
        return _page(request, "orders/review.html", context)
    wait = _budget(request, "place", PLACE_LIMIT)
    if wait:
        return _too_many(wait)
    preview = _preview(request, cart, data)
    if not preview.placeable or not _ordering_open(preview):
        return _page(request, "orders/review.html", _review_context(request, cart, data))
    try:
        order, _changes = place_order(preview, ctx=audit_context(request))
    except CheckoutClosed as closed:
        # ORD.10: a kill switch is off; nothing was saved. The message names no vendor.
        context = _review_context(request, cart, data, errors=[closed.customer_message])
        return _page(request, "orders/review.html", context)
    except OrderRefused as refused:
        context = _review_context(request, cart, data, errors=_refusal_messages(refused))
        return _page(request, "orders/review.html", context)
    if order is None:
        raise Http404
    request.session[state.CONFIRMATION_KEY] = str(order.public_id)
    state.clear(request)
    return _private(HttpResponseRedirect(reverse("orders:confirmation")))


@public_form(
    protection=Protection.ALLOW,
    purpose="Checkout review: choose the shipping option of a vendor package",
    task="ORD.5",
    reason=(
        "it only stores the visitor's choice in their own session and shows the preview; it "
        "writes no row and calls nothing external"
    ),
    rate_limit="per IP (cart budget)",
)
@require_POST
def shipping_choice(request: HttpRequest) -> HttpResponse:
    cart = current_cart(request)
    data = state.load(request)
    if cart is None or not cart.lines.exists() or not data.get("address"):
        return redirect("orders:cart")
    wait = _budget(request, "cart", CART_LIMIT)
    if wait:
        return _too_many(wait)
    data = _save_shipping(request, cart, data)
    if not _htmx(request):
        return _private(HttpResponseRedirect(reverse("orders:review")))
    context = _review_context(request, cart, data, partial=True)
    return _page(request, "orders/_review_body.html", context)


# --- Confirmation ----------------------------------------------------------------------------


@require_http_methods(["GET"])
def confirmation_page(request: HttpRequest) -> HttpResponse:
    public_id = request.session.get(state.CONFIRMATION_KEY)
    if not isinstance(public_id, str):
        raise Http404("No order was just placed")
    order = Order.objects.filter(public_id=public_id).first()
    if order is None:
        raise Http404("No order was just placed")
    context = {
        "order": order,
        "lines": [
            {"label": _("Goods, excluding VAT"), "amount": present.money(order.goods_net)},
            {"label": _("Shipping, excluding VAT"), "amount": present.money(order.shipping_net)},
            {"label": _("VAT"), "amount": present.money(order.tax_total)},
        ],
        "grand_total": present.money(order.grand_total),
    }
    return _page(request, "orders/confirmation.html", context)
