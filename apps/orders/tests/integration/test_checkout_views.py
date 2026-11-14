"""ORD.5: the cart and checkout pages, in the real request cycle.

Done-when (views): the cart groups lines by vendor package with the totals of `build_checkout`;
the details form asks for company and VAT id only for a business and checks the VAT id with VIES
(mocked through our own `ViesClient` interface); every blocking message of the preview is shown;
"place order" calls `place_order` only for a placeable preview; the forms are CSRF-checked and
behind the captcha where they cost something.
"""

import re
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.cart.models import Cart
from apps.cart.session import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core import flags
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, FeatureFlag
from apps.core.services import FLAG_CACHE, save_flag
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import checkout
from apps.orders.models import Order
from apps.orders.ui import state, views
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

D = Decimal
NBSP = "\N{NO-BREAK SPACE}"
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross",
}
DETAILS = {
    "email": "buyer@example.com",
    "full_name": "Mario Rossi",
    "line1": "1 Via Test",
    "postcode": "00100",
    "city": "Roma",
    "country": "IT",
}


@pytest.fixture(autouse=True)
def tax_setup() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    for country, category, rate in (
        ("IT", tax.STANDARD, "22"),
        ("IT", tax.REDUCED, "10"),
        ("DE", tax.STANDARD, "19"),
    ):
        TaxRateFactory.create(
            country=country, category=category, rate=D(rate), valid_from=utc(2026, 1, 1)
        )


def vendor(name: str = "Rossi Ceramics", *, shipping: bool = True, **fields: Any) -> Vendor:
    fields.setdefault("sells_online", True)  # a ready payout account (D-19, VEN.6)
    found = VendorFactory.create(
        status=Vendor.Status.ACTIVE, country="IT", display_name=name, **fields
    )
    VendorTaxProfileFactory.create(vendor=found, valid_from=utc(2026, 1, 1))
    if shipping:
        method = ShippingMethodFactory.create(
            vendor=found, zone=ShippingZoneFactory.create(countries=["IT", "DE"]), is_default=True
        )
        ShippingMethodTranslationFactory.create(method=method, name="Standard", status="published")
        WeightBandFactory.create(method=method, from_kg=D("0"), to_kg=D("5"), cost_net=D("9.90"))
        WeightBandFactory.create(method=method, from_kg=D("5"), to_kg=D("10"), cost_net=D("14.90"))
    return found


def add(cart: Cart, owner: Vendor, price: str, title: str = "Vase", **fields: Any) -> Product:
    values: dict[str, Any] = {
        "vendor": owner,
        "status": Product.Status.PUBLISHED,
        "price_net": D(price),
        "stock_qty": 100,
        "weight_kg": D("0.500"),
        **fields,
    }
    product = ProductFactory.create(**values)
    ProductTranslationFactory.create(product=product, title=title, status="published")
    CartLineFactory.create(cart=cart, product=product, quantity=1, added_price_net=D(price))
    return product


def with_cart(client: Client) -> Cart:
    cart = CartFactory.create()
    session = client.session
    session[GUEST_CART_SESSION_KEY] = cart.session_key
    session.save()
    return cart


def sign_off_details(client: Client, **extra: str) -> Any:
    return client.post(reverse("orders:checkout"), {**DETAILS, **extra})


@pytest.fixture
def commission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        state, "commission_snapshots", lambda ids: {pk: dict(COMMISSION) for pk in ids}
    )


# --- Cart -----------------------------------------------------------------------------------


def test_an_empty_cart_says_so(client: Client) -> None:
    response = client.get(reverse("orders:cart"))
    assert response.status_code == 200
    assert "Your cart is empty" in response.content.decode()


def test_the_cart_groups_lines_by_vendor_package(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "30.00", "Blue vase")
    add(cart, vendor("Beta Shop"), "20.00", "Red cup")
    html = client.get(reverse("orders:cart")).content.decode()
    assert html.index("Sold by Alpha Shop") < html.index("Blue vase")
    assert html.index("Blue vase") < html.index("Sold by Beta Shop") < html.index("Red cup")
    assert f"€{NBSP}30.00" in html  # unit price, excluding VAT
    assert "worked out at checkout" in html  # no address yet: no totals


def test_the_cart_shows_totals_from_the_preview_once_the_address_is_known(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "100.00")
    state_session = client.session
    state_session[state.SESSION_KEY] = {
        "address": {"line1": "1 Via Test", "city": "Roma", "postcode": "00100", "country": "IT"}
    }
    state_session.save()
    html = client.get(reverse("orders:cart")).content.decode()
    # goods 100.00 + shipping 9.90, VAT 22.00 + 2.18: the numbers of build_checkout
    assert f"€{NBSP}134.08" in html


def test_a_quantity_change_updates_the_cart_and_announces_it_with_htmx(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    line = cart.lines.get()
    response = client.post(
        reverse("orders:cart"), {f"qty_{line.public_id}": "3"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    line.refresh_from_db()
    assert line.quantity == 3
    html = response.content.decode()
    assert 'id="live-region" hx-swap-oob="innerHTML"' in html
    assert "Cart updated." in html
    assert "<html" not in html  # a fragment


def test_a_quantity_above_the_stock_is_refused_in_words(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00", stock_qty=2)
    line = cart.lines.get()
    response = client.post(
        reverse("orders:cart"), {f"qty_{line.public_id}": "9"}, HTTP_HX_REQUEST="true"
    )
    line.refresh_from_db()
    assert line.quantity == 1
    assert "Cart updated." not in response.content.decode()


def test_a_line_is_removed_without_javascript(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    line = cart.lines.get()
    response = client.post(reverse("orders:cart"), {"remove": str(line.public_id)})
    assert response.status_code == 302
    assert not cart.lines.exists()


def test_the_cart_names_blocking_reasons_and_offers_a_quote(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor("Ask Shop", shipping=False), "0.00", "Rare part",
        sales_mode=Product.SalesMode.ON_REQUEST, price_net=None)  # fmt: skip
    html = client.get(reverse("orders:cart")).content.decode()
    assert "sold on request only" in html
    assert "Request a quote" in html
    assert "Continue to checkout" not in html


# --- Details --------------------------------------------------------------------------------


def test_checkout_without_a_cart_goes_back_to_the_cart(client: Client) -> None:
    assert client.get(reverse("orders:checkout")).status_code == 302


def test_the_details_form_has_labelled_fields_and_business_fields(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    html = client.get(reverse("orders:checkout")).content.decode()
    for label in ("Email address", "Full name", "Postcode", "Company name", "VAT number"):
        assert f">{label}" in html
    assert 'autocomplete="postal-code"' in html
    assert "checkout-business" in html


def test_business_fields_are_required_only_for_a_business(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    ok = sign_off_details(client)
    assert ok.status_code == 302
    assert ok["Location"] == reverse("orders:review")
    bad = sign_off_details(client, is_business="on")
    assert bad.status_code == 200
    html = bad.content.decode()
    assert "There is a problem" in html
    assert html.count("Enter this when you buy for a business") >= 2
    assert 'aria-invalid="true"' in html


def test_a_malformed_vat_id_is_refused_before_vies_is_asked(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    bad = sign_off_details(client, is_business="on", company="Acme GmbH", vat_id="DE#12345")
    assert bad.status_code == 200
    assert "does not look like an EU VAT number" in bad.content.decode()


def test_a_valid_business_vat_id_is_checked_with_vies_as_each_vendor(
    client: Client, settings: Any
) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    response = sign_off_details(
        client, is_business="on", company="Acme GmbH", vat_id="DE123456789", country="DE"
    )
    assert response.status_code == 302
    saved = client.session[state.SESSION_KEY]
    assert saved["vat_ok"] is True
    assert saved["vat_validation"]
    html = client.get(reverse("orders:review")).content.decode()
    assert "Your VAT number is valid" in html


def _spy_previews(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, Any]]:
    """Record the VIES answer each preview was built with, and the preview."""
    seen: list[tuple[Any, Any]] = []
    real = checkout.build_checkout

    def spy(*args: Any, **kwargs: Any) -> Any:
        preview = real(*args, **kwargs)
        seen.append((kwargs.get("customer_vat"), preview))
        return preview

    monkeypatch.setattr(views, "build_checkout", spy)
    return seen


def test_a_vendor_added_after_the_vies_check_gets_no_reverse_charge(
    client: Client, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-28: the VIES answer is proof only for the vendors it was asked as. A vendor added to the
    cart after the details step has no consultation of its own: VAT is charged on everything
    until the buyer confirms the details again."""
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(
        client, is_business="on", company="Acme GmbH", vat_id="DE123456789", country="DE"
    )
    add(cart, vendor("Beta Shop"), "50.00")
    seen = _spy_previews(monkeypatch)
    html = client.get(reverse("orders:review")).content.decode()
    customer_vat, preview = seen[-1]
    assert customer_vat is None
    assert preview.customer_tax_status == "b2c"
    assert all(p.totals is not None and p.totals.tax_total > 0 for p in preview.packages)
    assert "Confirm your details again" in html
    assert "Your VAT number is valid" not in html
    # Confirming again checks VIES as both vendors: the reverse charge is back.
    sign_off_details(
        client, is_business="on", company="Acme GmbH", vat_id="DE123456789", country="DE"
    )
    client.get(reverse("orders:review"))
    customer_vat, preview = seen[-1]
    assert customer_vat is not None
    assert preview.customer_tax_status == "b2b"


def test_a_two_vendor_cart_checked_as_both_vendors_is_reverse_charged_for_both(
    client: Client, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.ValidViesClient"
    cart = with_cart(client)
    alpha, beta = vendor("Alpha Shop"), vendor("Beta Shop")
    add(cart, alpha, "100.00")
    add(cart, beta, "50.00")
    sign_off_details(
        client, is_business="on", company="Acme GmbH", vat_id="DE123456789", country="DE"
    )
    saved = client.session[state.SESSION_KEY]
    assert sorted(saved["vat_vendors"]) == sorted([alpha.pk, beta.pk])
    seen = _spy_previews(monkeypatch)
    html = client.get(reverse("orders:review")).content.decode()
    customer_vat, preview = seen[-1]
    assert customer_vat is not None
    assert preview.customer_tax_status == "b2b"
    assert len(preview.packages) == 2
    assert all(p.totals is not None and p.totals.tax_total == 0 for p in preview.packages)
    assert "Your VAT number is valid" in html


@pytest.mark.parametrize(
    ("client_path", "message"),
    [
        ("apps.tax.tests.fakes.DownViesClient", "could not confirm your VAT number"),
    ],
)
def test_vies_down_keeps_the_vat_and_says_so(
    client: Client, settings: Any, client_path: str, message: str
) -> None:
    settings.VIES_CLIENT = client_path
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    sign_off_details(client, is_business="on", company="Acme GmbH", vat_id="DE123456789",
                     country="DE")  # fmt: skip
    assert client.session[state.SESSION_KEY]["vat_ok"] is False
    assert message in client.get(reverse("orders:review")).content.decode()


# --- Review and order -----------------------------------------------------------------------


def test_the_review_shows_shipping_choice_totals_and_the_pay_step(
    client: Client, commission: None
) -> None:
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    html = client.get(reverse("orders:review")).content.decode()
    assert "Sold by Alpha Shop" in html
    assert 'type="radio"' in html
    assert "Standard" in html
    assert "134.08" in html
    assert "Confirm order and pay now" in html


def test_the_shipping_choice_is_kept_and_answers_a_fragment_for_htmx(
    client: Client, commission: None
) -> None:
    cart = with_cart(client)
    found = vendor("Alpha Shop")
    add(cart, found, "100.00")
    sign_off_details(client)
    response = client.post(
        reverse("orders:shipping"), {f"ship_{found.public_id}": "unknown"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert "Choose one of the shipping options" in response.content.decode()
    assert 'id="live-region" hx-swap-oob' in response.content.decode()
    assert client.post(reverse("orders:shipping"), {}).status_code == 302


def test_a_package_that_needs_a_quote_cannot_be_ordered(client: Client, commission: None) -> None:
    cart = with_cart(client)
    add(cart, vendor("Heavy Shop"), "10.00", "Anvil", weight_kg=D("12.000"))
    sign_off_details(client)
    html = client.get(reverse("orders:review")).content.decode()
    assert "needs a quote" in html
    assert "Request a quote" in html
    assert "Confirm order and pay now" not in html
    assert client.post(reverse("orders:review")).status_code == 200
    assert not Order.objects.exists()


def test_out_of_stock_and_a_closed_checkout_are_named(client: Client, commission: None) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00", "Last vase", stock_qty=0, stock_policy=Product.StockPolicy.TRACK)
    sign_off_details(client)
    assert "out of stock" in client.get(reverse("orders:review")).content.decode()
    FeatureFlag.objects.filter(name="checkout").update(state=FeatureFlag.State.OFF)
    assert "Ordering is not available" in client.get(reverse("orders:review")).content.decode()


def test_ordering_is_closed_while_no_commission_rule_exists(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    sign_off_details(client)
    html = client.get(reverse("orders:review")).content.decode()
    assert "Ordering is not open yet" in html
    assert "Confirm order and pay now" not in html
    client.post(reverse("orders:review"))
    assert not Order.objects.exists()


def test_place_order_runs_for_a_placeable_preview_and_shows_the_confirmation(
    client: Client, commission: None
) -> None:
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    response = client.post(reverse("orders:review"), {"accept_terms": "on"})
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:confirmation")
    order = Order.objects.get()
    assert order.grand_total == D("134.08")
    assert order.email == "buyer@example.com"
    page = client.get(reverse("orders:confirmation")).content.decode()
    assert order.number in page
    assert "Online payment is not available yet" in page
    assert state.SESSION_KEY not in client.session


@pytest.mark.parametrize(
    ("switch", "message"),
    [
        # The preview itself already names a closed checkout switch (ORD.5), before any order.
        (flags.CHECKOUT, "Ordering is not available"),
        # The payments switch is only read by `place_order` (ORD.10): the view must turn its
        # `CheckoutClosed` into the customer message instead of a server error.
        (flags.PAYMENTS, "We are not taking new orders right now"),
    ],
)
def test_a_pulled_switch_shows_a_customer_message_and_places_nothing(
    client: Client, commission: None, switch: str, message: str
) -> None:
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    FLAG_CACHE.clear()
    row = FeatureFlag.objects.get(name=switch)
    row.state = flags.OFF
    staff = AuditContext(
        actor_label="staff:ops@example.com", source=AuditLog.Source.ADMIN, actor_id=7
    )
    save_flag(row, ctx=staff)
    FLAG_CACHE.clear()
    try:
        response = client.post(reverse("orders:review"), {"accept_terms": "on"})
    finally:
        FLAG_CACHE.clear()
    assert response.status_code == 200
    assert message in response.content.decode()
    assert not Order.objects.exists()


def test_the_confirmation_needs_the_order_just_placed(client: Client) -> None:
    assert client.get(reverse("orders:confirmation")).status_code == 404


# --- Protection -----------------------------------------------------------------------------


class _Containers(HTMLParser):
    """The ids of the elements open around each element with a given class."""

    VOID = frozenset({"area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"})

    def __init__(self, css_class: str) -> None:
        super().__init__()
        self.css_class = css_class
        self.stack: list[tuple[str, str]] = []
        self.found: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self.css_class in (values.get("class") or "").split():
            self.found.append([tag_id or tag_name for tag_name, tag_id in self.stack])
        if tag not in self.VOID:
            self.stack.append((tag, values.get("id") or ""))

    def handle_endtag(self, tag: str) -> None:
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def containers(html: str, css_class: str) -> list[list[str]]:
    parser = _Containers(css_class)
    parser.feed(html)
    return parser.found


def test_the_captcha_widget_is_outside_the_shipping_swap_target(
    client: Client, settings: Any, commission: None
) -> None:
    """A shipping change swaps #review-body. Turnstile renders its widgets once, when its script
    loads, so a widget inside the swap target would come back empty and the next "Place order"
    would carry no token. The widget stays put; the fragment updates only the actions."""
    cart = with_cart(client)
    found = vendor("Alpha Shop")
    add(cart, found, "100.00")
    sign_off_details(client)
    settings.CAPTCHA_ENABLED = True
    settings.TURNSTILE_SITE_KEY = "test-site-key"
    html = client.get(reverse("orders:review")).content.decode()
    [around] = containers(html, "cf-turnstile")
    assert "form" in around  # posted with the order
    assert "review-body" not in around
    assert "review-actions" not in around
    fragment = client.post(
        reverse("orders:shipping"), {f"ship_{found.public_id}": "unknown"}, HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "cf-turnstile" not in fragment
    assert 'id="review-actions" hx-swap-oob="true"' in fragment


def test_the_forms_are_csrf_checked() -> None:
    strict = Client(enforce_csrf_checks=True)
    for name in ("orders:cart", "orders:checkout", "orders:review", "orders:shipping"):
        assert strict.post(reverse(name), {}).status_code == 403


def test_the_details_and_the_order_refuse_a_failed_captcha(
    client: Client, settings: Any, commission: None
) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    sign_off_details(client)
    settings.CAPTCHA_ENABLED = True
    settings.CAPTCHA_VERIFIER = "apps.accounts.tests.fakes.FakeCaptchaVerifier"
    settings.TURNSTILE_SITE_KEY = "test-site-key"
    FakeCaptchaVerifier.reset()
    refused = client.post(reverse("orders:checkout"), {**DETAILS, TOKEN_FIELD: "wrong"})
    assert refused.status_code == 200
    assert "verification failed" in refused.content.decode()
    client.post(reverse("orders:review"), {TOKEN_FIELD: "wrong"})
    assert not Order.objects.exists()
    assert FakeCaptchaVerifier.calls
    FakeCaptchaVerifier.reset()


def test_pages_have_no_inline_script_and_are_not_cached_or_indexed(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    sign_off_details(client)
    for name in ("orders:cart", "orders:checkout", "orders:review"):
        response = client.get(reverse(name))
        html = response.content.decode()
        assert "noindex" in html
        assert "no-store" in response["Cache-Control"] or "no-cache" in response["Cache-Control"]
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert " src=" in tag, f"inline script on {name}: {tag}"


def test_the_cart_updates_have_a_per_ip_budget(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "CART_LIMIT", 2)
    with_cart(client)
    for _ in range(2):
        assert client.post(reverse("orders:cart"), {}).status_code == 302
    limited = client.post(reverse("orders:cart"), {})
    assert limited.status_code == 429
    assert int(limited["Retry-After"]) >= 1
