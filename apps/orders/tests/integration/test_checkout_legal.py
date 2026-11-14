"""ORD.17: the checkout legal step (owner decision D-40, counsel variant A).

Done-when: per-seller recap with the link to the terms version in force; box 1 mandatory and
unticked for every buyer, enforced on the server; the order keeps the platform version, one
version per seller and the time; the privacy notice is a link, never a box; box 2 only for a
business buyer when the operator is established in Italy or a seller in the cart is established
in Italy or chooses Italian law (never for a consumer, never a literal country); courts; the
button names the obligation to pay; the confirmation email carries the terms as text.
"""

import datetime as dt
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale  # noqa: F401  (the primary locale exists in the test database)

from apps.cms.models import LegalPage
from apps.cms.tests.factories import LegalPageFactory
from apps.core.models import OutboxEmail, PlatformOperator
from apps.orders import legal
from apps.orders.models import Order
from apps.orders.tests.integration.test_checkout_views import (  # noqa: F401 (fixtures)
    DETAILS,
    NBSP,
    add,
    commission,
    sign_off_details,
    tax_setup,
    vendor,
    with_cart,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import SalesTermsVersionFactory

pytestmark = pytest.mark.django_db

BUSINESS: dict[str, str] = {
    "is_business": "on",
    "company": "Acme GmbH",
    "vat_id": "DE123456789",
    "country": "DE",
}
BOX1 = {"accept_terms": "on"}
BOX2 = {"accept_terms": "on", "accept_onerous": "on"}
PLATFORM_CLAUSES = [{"article": "12", "title": "Competent court of the operator"}]


@pytest.fixture(autouse=True)
def vies_down(settings: Any) -> None:
    settings.VIES_CLIENT = "apps.tax.tests.fakes.DownViesClient"  # the VIES result must not matter


def publish_terms(
    clauses: list[dict[str, str]] | None = None, body: str = "Platform terms text"
) -> LegalPage:
    page = LegalPageFactory.create(
        kind=LegalPage.Kind.TERMS,
        effective_date=dt.date(2026, 1, 1),
        body=[("paragraph", f"<p>{body}</p>")],
        onerous_clauses=clauses if clauses is not None else PLATFORM_CLAUSES,
    )
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def seller(name: str, *, country: str = "IT", law: str = "IT", **terms: Any) -> Vendor:
    found = vendor(name)
    if country != "IT":
        Vendor.objects.filter(pk=found.pk).update(country=country, registered_country=country)
        found.refresh_from_db()
    terms.setdefault("clauses", [{"article": "7", "title": f"Liability of {name}"}])
    SalesTermsVersionFactory.create(
        vendor=found, governing_law=law, body=f"Terms of {name}", **terms
    )
    return found


def operator(country: str) -> None:
    PlatformOperator.objects.update(country=country)


def review(client: Client, **details: str) -> str:
    sign_off_details(client, **details)
    response = client.get(reverse("orders:review"))
    assert response.status_code == 200
    return str(response.content.decode())


def place(client: Client, boxes: dict[str, str]) -> Any:
    return client.post(reverse("orders:review"), boxes)


def checkboxes(html: str) -> list[str]:
    """The checkboxes of the review form (the cookie banner has its own)."""
    start = html.index(f'action="{reverse("orders:review")}"')
    form = html[start : html.index("</form>", start)]
    return re.findall(r'<input type="checkbox" id="([^"]+)"', form)


# --- Recap, box 1, the order's snapshot -----------------------------------------------------


def test_each_seller_has_a_recap_block_with_a_link_to_the_terms_version_in_force(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    cart = with_cart(client)
    alpha, beta = seller("Alpha Shop"), seller("Beta Shop")
    add(cart, alpha, "30.00", "Blue vase")
    add(cart, beta, "20.00", "Red cup")
    html = review(client)
    assert html.count('class="legal-recap__seller"') == 2
    for shop in (alpha, beta):
        link = re.search(
            rf'<a href="(/[^"]*terms/[^"]+)"[^>]*>Sales terms of {shop.display_name} \(version 1',
            html,
        )
        assert link, shop.display_name
    assert "professional seller" in html
    assert "Delivery time" in html
    assert "Legal guarantee" in html


def test_the_order_cannot_be_placed_without_box_1_and_nothing_is_saved(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    add(with_cart(client), seller("Alpha Shop"), "30.00")
    html = review(client)
    assert checkboxes(html) == ["accept-terms"]
    assert 'id="accept-terms" name="accept_terms" required' in html
    assert "checked" not in re.search(r'<input type="checkbox" id="accept-terms"[^>]*>', html)[0]  # type: ignore[index]
    refused = place(client, {})
    assert refused.status_code == 200
    assert "Tick the box to accept the terms" in refused.content.decode()
    assert not Order.objects.exists()
    assert place(client, {"accept_terms": "off"}).status_code == 200
    assert not Order.objects.exists()


def test_the_order_keeps_the_platform_version_one_version_per_seller_and_the_time(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    page = publish_terms()
    cart = with_cart(client)
    alpha, beta = seller("Alpha Shop"), seller("Beta Shop")
    add(cart, alpha, "30.00", "Blue vase")
    add(cart, beta, "20.00", "Red cup")
    review(client)
    assert place(client, BOX1).status_code == 302
    order = Order.objects.get()
    kept = order.terms_acceptance
    assert order.terms_accepted_at is not None
    version = page.versions.get()
    assert kept["platform"]["version_id"] == version.pk
    assert kept["platform"]["number"] == 1
    assert len(kept["platform"]["sha256"]) == 64
    by_vendor = {entry["vendor"]: entry for entry in kept["sellers"]}
    assert set(by_vendor) == {str(alpha.public_id), str(beta.public_id)}
    for shop in (alpha, beta):
        stored = shop.sales_terms_in_force.version
        entry = by_vendor[str(shop.public_id)]
        assert entry["version_id"] == stored.pk
        assert entry["content_hash"] == stored.content_hash
    assert kept["box1"] is True
    assert kept["accepted_at"]


def test_a_new_terms_version_after_the_order_does_not_change_what_the_order_kept(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    shop = seller("Alpha Shop")
    add(with_cart(client), shop, "30.00")
    review(client)
    place(client, BOX1)
    order = Order.objects.get()
    first = order.terms_acceptance["sellers"][0]["version_id"]
    SalesTermsVersionFactory.create(vendor=shop, body="Second version")
    order.refresh_from_db()
    assert order.terms_acceptance["sellers"][0]["version_id"] == first


def test_the_privacy_notice_is_a_link_never_a_box(client: Client, commission: None) -> None:  # noqa: F811
    privacy = LegalPageFactory.create(
        kind=LegalPage.Kind.PRIVACY,
        title="Privacy",
        slug="privacy",
        effective_date=dt.date(2026, 1, 1),
    )
    privacy.save_revision().publish()
    publish_terms()
    add(with_cart(client), seller("Alpha Shop"), "30.00")
    html = review(client)
    assert re.search(r'<a href="/[^"]*privacy[^"]*"[^>]*>Privacy notice</a>', html)
    assert checkboxes(html) == ["accept-terms"]  # the only box is box 1
    assert not re.search(r'name="[^"]*privacy[^"]*"', html)


def test_the_button_names_the_obligation_to_pay_and_the_total(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    add(with_cart(client), seller("Alpha Shop"), "100.00")
    html = review(client)
    assert "Confirm order and pay now – EUR 134.08" in html  # noqa: RUF001
    assert "Place order" not in html
    assert "Complete order" not in html


# --- Box 2: the full matrix --------------------------------------------------------------------


def test_a_consumer_never_sees_box_2_also_with_an_italian_seller_on_an_italian_operator(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    operator("IT")
    add(with_cart(client), seller("Alpha Shop"), "30.00")
    html = review(client)
    assert checkboxes(html) == ["accept-terms"]
    assert "articles 1341 and 1342" not in html
    assert place(client, BOX1).status_code == 302  # box 2 is not required of a consumer


def test_a_business_with_an_unverified_vat_number_sees_box_2_and_must_tick_it(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    operator("IT")
    add(with_cart(client), seller("Alpha Shop"), "30.00")
    html = review(client, **BUSINESS)
    assert client.session["checkout_state"]["vat_ok"] is False  # VIES did not confirm it
    assert checkboxes(html) == ["accept-terms", "accept-onerous"]
    assert "Article 12: Competent court of the operator" in html
    assert "Article 7: Liability of Alpha Shop" in html
    refused = place(client, BOX1)
    assert refused.status_code == 200
    assert not Order.objects.exists()
    assert "second box" in refused.content.decode()
    assert place(client, BOX2).status_code == 302
    box2 = Order.objects.get().terms_acceptance["box2"]
    assert box2["shown"]
    assert box2["accepted"]
    assert any("Competent court of the operator" in line for line in box2["text"])
    assert any("Liability of Alpha Shop" in line for line in box2["text"])


def test_a_foreign_operator_and_a_foreign_seller_show_no_box_2_to_a_business(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    operator("DE")
    add(with_cart(client), seller("Alpha Shop", country="DE", law="DE"), "30.00")
    html = review(client, **BUSINESS)
    assert checkboxes(html) == ["accept-terms"]
    assert place(client, BOX1).status_code == 302
    assert Order.objects.get().terms_acceptance["box2"] == {
        "shown": False,
        "accepted": False,
        "text": [],
    }


def test_a_foreign_operator_lists_only_the_italian_sellers_of_the_cart(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    operator("DE")
    cart = with_cart(client)
    add(cart, seller("Italian Shop"), "30.00", "Blue vase")
    add(cart, seller("German Shop", country="DE", law="DE"), "20.00", "Red cup")
    add(cart, seller("Chosen Shop", country="DE", law="IT"), "10.00", "Green jug")
    html = review(client, **BUSINESS)
    assert checkboxes(html) == ["accept-terms", "accept-onerous"]
    assert "Liability of Italian Shop" in html
    assert "Liability of Chosen Shop" in html  # a foreign seller whose terms choose Italian law
    assert "Liability of German Shop" not in html
    assert "Competent court of the operator" not in html  # the operator is not Italian


def test_an_italian_operator_lists_its_own_clauses_for_a_business_buying_from_abroad(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    operator("IT")
    add(with_cart(client), seller("German Shop", country="DE", law="DE"), "30.00")
    html = review(client, **BUSINESS)
    assert checkboxes(html) == ["accept-terms", "accept-onerous"]
    assert "Competent court of the operator" in html
    assert "Liability of German Shop" not in html


# --- Courts ------------------------------------------------------------------------------------


def test_a_consumer_sees_the_court_of_residence_and_a_business_the_seller_court(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    add(with_cart(client), seller("Alpha Shop", b2b_court="Milano"), "30.00")
    consumer = review(client)
    assert "the court of your own place of residence" in consumer
    assert "Court of Milano" not in consumer
    business = review(client, **BUSINESS)
    assert "Court of Milano" in business
    assert "own place of residence" not in business


# --- A seller without terms, a site without platform terms -------------------------------------


def test_a_seller_without_terms_is_shown_as_such_and_does_not_block_the_order(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms()
    add(with_cart(client), vendor("Plain Shop"), "30.00")  # no terms version, setting off
    html = review(client)
    assert "No seller terms published." in html
    assert place(client, BOX1).status_code == 302
    entry = Order.objects.get().terms_acceptance["sellers"][0]
    assert entry["version_id"] is None


def test_without_a_published_customer_terms_page_the_step_still_works(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    add(with_cart(client), seller("Alpha Shop"), "30.00")
    review(client)
    assert place(client, BOX1).status_code == 302
    assert Order.objects.get().terms_acceptance["platform"] is None


# --- The confirmation email --------------------------------------------------------------------


def test_the_confirmation_email_carries_the_terms_text_in_the_versions_of_the_order(
    client: Client,
    commission: None,  # noqa: F811
) -> None:
    publish_terms(body="Marketplace terms: exact words")
    shop = seller("Alpha Shop")
    add(with_cart(client), shop, "30.00")
    review(client)
    place(client, BOX1)
    SalesTermsVersionFactory.create(vendor=shop, body="A later version, not accepted")
    email = OutboxEmail.objects.get(template_key="orders.order_received")
    context = email.context_data
    assert "Marketplace terms: exact words" in context["terms"]["platform"]["text"]
    assert context["terms"]["sellers"][0]["text"] == "Terms of Alpha Shop"
    from apps.core.services import render_email

    rendered = render_email("orders.order_received", "en", context)
    assert "Marketplace terms: exact words" in rendered.text_body
    assert "Terms of Alpha Shop" in rendered.text_body
    assert "A later version" not in rendered.text_body
    assert "Terms of Alpha Shop" in rendered.html_body  # also in the HTML part


def test_the_step_texts_are_built_from_the_pure_rules_only(client: Client) -> None:
    """`build_step` for a consumer lists no clause group whatever the parties are."""
    step = legal.build_step([], language="en", business=False)
    assert not step.box2_required
    assert step.sellers == []
    assert NBSP
    assert DETAILS
