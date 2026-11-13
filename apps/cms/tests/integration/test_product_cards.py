"""CMS.11: the product cards block (spec 10 §10.2), and its place next to the CMS.10 grid.

Done when: "The same block works on all four page types; a retired or unpublished product
disappears from every page without editing it; an `on_request` product shows no price;
`graph_nodes` returns a named `ItemList` whose items are product `@id`s without offers". And the
SEO.7 row: a page with a product grid and a product cards block has two named `ItemList`s, and a
retired product disappears from both.

The block shows the hand-picked products that are published, of an active vendor and published in
the page's language (no text from another language), in the order the editor chose them. A block
with none left is not rendered.
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms.blocks import PRODUCT_CARDS_LAYOUTS, ProductCardsBlock, page_graph_nodes
from apps.cms.models import BlogPostPage, HomePage, StandardPage, TermLandingPage, VendorLandingPage
from apps.cms.services import SeedReport
from apps.cms.tests.integration.test_product_grid import _grid, _home, _publish, listed
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _cards(
    *products: Product, heading: str = "Picked for you", layout: str = "grid"
) -> tuple[str, dict[str, Any]]:
    return (
        "product_cards",
        {"heading": heading, "layout": layout, "products": list(products)},
    )


def _section(html: BeautifulSoup) -> Any:
    return html.find("section", class_="block-product-cards")


def _titles(html: BeautifulSoup) -> list[str]:
    section = _section(html)
    assert section is not None
    return [h.get_text(strip=True) for h in section.find_all(class_="product-tile__name")]


# --- Rendering


def test_the_cards_show_the_chosen_products_in_order(seeded: SeedReport) -> None:
    first, second, third = listed("Blue bowl"), listed("Red jug"), listed("Tin cup")
    html = _publish(_home(), [_cards(third, first, second)])
    assert _titles(html) == ["Tin cup", "Blue bowl", "Red jug"]
    heading = _section(html).find("h2")
    assert heading is not None
    assert heading.get_text() == "Picked for you"
    assert "style=" not in str(html)


def test_the_row_layout_is_a_class_not_a_style(seeded: SeedReport) -> None:
    html = _publish(_home(), [_cards(listed("Blue bowl"), layout="row")])
    section = _section(html)
    assert "block-product-cards--row" in section["class"]
    assert "style=" not in str(html)
    assert set(PRODUCT_CARDS_LAYOUTS) == {"grid", "row"}


def test_a_retired_draft_or_closed_vendor_product_disappears(seeded: SeedReport) -> None:
    shown = listed("Blue bowl")
    retired = listed("Old jug", status=Product.Status.RETIRED)
    draft = listed("Draft cup", status=Product.Status.DRAFT)
    closed = listed("Closed pan", vendor=VendorFactory.create(status=Vendor.Status.CLOSED))
    html = _publish(_home(), [_cards(shown, retired, draft, closed)])
    assert _titles(html) == ["Blue bowl"]
    # Retiring the shown one later removes the whole block without editing the page.
    Product.objects.filter(pk=shown.pk).update(status=Product.Status.RETIRED)
    again = BeautifulSoup(Client().get(_home().url).content, "html.parser")
    assert _section(again) is None


def test_only_products_published_in_the_page_language_are_shown(seeded: SeedReport) -> None:
    both = listed("Blue bowl")
    english_only = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
        status=Product.Status.PUBLISHED,
    )
    ProductTranslationFactory.create(
        product=english_only, title="Red jug", slug="red-jug-en", status=PUBLISHED
    )
    html = _publish(_home(), [_cards(both, english_only)])
    assert _titles(html) == ["Blue bowl", "Red jug"]
    italian = _home("it")
    italian.body = [_cards(both, english_only)]
    italian.save_revision().publish()
    response = Client().get(italian.url)
    assert _titles(BeautifulSoup(response.content, "html.parser")) == ["Blue bowl IT"]


def test_an_on_request_product_shows_no_price(seeded: SeedReport) -> None:
    for_sale = listed("Blue bowl", price_net=Decimal("10.00"))
    on_request = listed("Red jug", sales_mode=Product.SalesMode.ON_REQUEST, price_net=None)
    catalog_only = listed("Tin cup", sales_mode=Product.SalesMode.CATALOG_ONLY, price_net=None)
    html = _publish(_home(), [_cards(for_sale, on_request, catalog_only)])
    tiles = _section(html).find_all("li")
    assert len(tiles) == 3
    assert "10.00" in tiles[0].get_text()
    assert "EUR" not in tiles[1].get_text()
    assert "EUR" not in tiles[2].get_text()


def test_a_block_with_no_public_product_is_left_out(seeded: SeedReport) -> None:
    draft = listed("Draft cup", status=Product.Status.DRAFT)
    html = _publish(_home(), [_cards(draft, heading="Nothing here")])
    assert _section(html) is None
    assert "Nothing here" not in str(html)


def test_the_block_needs_at_least_one_product() -> None:
    block = ProductCardsBlock()
    assert block.child_blocks["products"].meta.min_num == 1


def test_the_cards_cost_the_same_queries_for_more_products(seeded: SeedReport) -> None:
    home = _home()
    home.body = [_cards(*[listed(f"Bowl {n}") for n in range(2)])]
    home.save_revision().publish()
    client = Client()
    client.get(home.url)  # warm caches that are not about the block
    with CaptureQueriesContext(connection) as few:
        client.get(home.url)
    home.body = [_cards(*[listed(f"Cup {n}") for n in range(8)])]
    home.save_revision().publish()
    client.get(home.url)
    with CaptureQueriesContext(connection) as many:
        client.get(home.url)
    assert len(many) <= len(few) + 1


# --- Placement (spec 10 page table; the owner wants the same element on every body)


@pytest.mark.parametrize("page_model", [HomePage, StandardPage, BlogPostPage, VendorLandingPage])
def test_the_cards_and_the_grid_are_offered_in_these_bodies(page_model: Any) -> None:
    children = page_model._meta.get_field("body").stream_block.child_blocks
    assert isinstance(children["product_cards"], ProductCardsBlock)
    assert "product_grid" in children


def test_the_cards_are_offered_in_a_term_landing_body() -> None:
    children = TermLandingPage._meta.get_field("body").stream_block.child_blocks
    assert "product_cards" in children


def test_legal_pages_do_not_offer_product_blocks() -> None:
    from apps.cms.models import LegalPage

    children = LegalPage._meta.get_field("body").stream_block.child_blocks
    assert "product_cards" not in children
    assert "product_grid" not in children


def test_the_cards_render_on_a_standard_page(seeded: SeedReport) -> None:
    about = StandardPage.objects.get(locale__language_code="en", depth=_home().depth + 1)
    html = _publish(about, [_cards(listed("Blue bowl"))])
    assert _titles(html) == ["Blue bowl"]


# --- SEO.7: named ItemLists (graph_nodes contract, spec 10 "Widgets feed the graph")


def _lists(home: HomePage, body: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    home.body = body
    home.save_revision().publish()
    home.refresh_from_db()
    from django.test import RequestFactory

    request = RequestFactory().get(home.url)
    return page_graph_nodes(home.body, {"request": request, "page": home})


def test_a_grid_and_a_cards_block_give_two_named_item_lists(seeded: SeedReport) -> None:
    gridded = listed("Grid bowl")
    picked = listed("Picked jug")
    vendor = gridded.vendor
    nodes = _lists(
        _home(),
        [_grid("vendor", vendor, heading="From the workshop"), _cards(picked, heading="Our picks")],
    )
    assert [node["@type"] for node in nodes] == ["ItemList", "ItemList"]
    assert [node["name"] for node in nodes] == ["From the workshop", "Our picks"]
    assert [node["numberOfItems"] for node in nodes] == [1, 1]
    for node in nodes:
        element = node["itemListElement"][0]
        assert element["@type"] == "ListItem"
        assert element["position"] == 1
        assert element["item"]["@id"].startswith("http")
        assert "offers" not in str(node).lower()
    assert nodes[0]["itemListElement"][0]["item"]["name"] == "Grid bowl"
    assert nodes[1]["itemListElement"][0]["item"]["name"] == "Picked jug"


def test_a_retired_product_leaves_both_item_lists(seeded: SeedReport) -> None:
    kept = listed("Kept bowl")
    gone = listed("Gone jug", vendor=kept.vendor)
    body = [_grid("vendor", kept.vendor, heading="Grid"), _cards(kept, gone, heading="Cards")]
    home = _home()
    assert [n["numberOfItems"] for n in _lists(home, body)] == [2, 2]
    Product.objects.filter(pk=gone.pk).update(status=Product.Status.RETIRED)
    after = _lists(_home(), body)
    assert [n["numberOfItems"] for n in after] == [1, 1]
    assert "Gone jug" not in str(after)


def test_an_item_list_is_named_even_when_the_heading_is_empty(seeded: SeedReport) -> None:
    nodes = _lists(_home(), [_cards(listed("Blue bowl"), heading="")])
    assert nodes[0]["name"]


def test_a_product_name_cannot_break_out_of_the_json(seeded: SeedReport) -> None:
    hostile = listed("</script><img src=x onerror=alert(1)>")
    nodes = _lists(_home(), [_cards(hostile)])
    from apps.cms.engine import jsonld_text

    text = jsonld_text({"@graph": nodes})
    assert "</script>" not in text
    assert "<img" not in text


# --- Limit and one computation per request


def test_the_block_takes_at_most_twelve_products() -> None:
    from django.core.exceptions import ValidationError

    products = ProductCardsBlock().child_blocks["products"]
    assert products.meta.max_num == 12
    assert "12" in str(products.meta.help_text)
    block = ProductCardsBlock()
    too_many = [listed(f"Bowl {n}") for n in range(13)]
    value = block.to_python({"heading": "", "layout": "grid", "products": [p.pk for p in too_many]})
    with pytest.raises(ValidationError):
        block.clean(value)


def test_the_cards_are_computed_once_for_the_page_and_the_graph(
    seeded: SeedReport, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.test import RequestFactory

    import apps.catalog.services as catalog_services
    import apps.cms.blocks as blocks_module

    calls: list[int] = []
    real = catalog_services.product_cards

    def counting(products: Any, language: str) -> Any:
        calls.append(1)
        return real(products, language)

    monkeypatch.setattr(blocks_module, "product_cards", counting)
    home = _home()
    home.body = [_cards(listed("Blue bowl"))]
    home.save_revision().publish()
    home.refresh_from_db()
    request = RequestFactory().get(home.url)
    context = {"request": request, "page": home}
    block = home.body[0]
    block.block.get_context(block.value, parent_context=context)
    page_graph_nodes(home.body, context)
    assert len(calls) == 1
