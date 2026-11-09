"""CAT.6: the `Redirect` model, its audited services, the 404 middleware and the map import.

Spec 03 §3.1 lists the legacy redirect rules of the four presentation mu-plugins; each one has a
case here:

- a retired SKU's page → the replacement product, or a tag (term) page;
- a recently merged tag → the surviving tag's page;
- an old discontinued category → a search;
- `/author/*` → the home page (a prefix rule, per language);
- GET `?add-to-cart=` → the product page (never adds to the cart).

A rule acts only on a 404: a page that exists always wins. A redirect never leaves the site,
never loops and never chains (one hop), and a 410 answers "gone".
"""

import datetime as dt
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import QuerySet
from django.test import Client
from django.utils import timezone

from apps.catalog.models import Product, Redirect
from apps.catalog.services import (
    delete_redirect,
    import_redirects,
    resolve_legacy_path,
    save_redirect,
)
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    SkuAliasFactory,
    SlugHistoryFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, TranslationStatus
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
PUBLISHED = TranslationStatus.PUBLISHED


def entries() -> "QuerySet[AuditLog]":
    return AuditLog.objects.filter(action__startswith="catalog.redirect.")


def assert_301(response: Any, location: str) -> None:
    assert response.status_code == 301, response.status_code
    assert response["Location"] == location


@pytest.fixture
def tags() -> dict[str, Any]:
    """A landing taxonomy "tags" (IT "etichette") with a "bags" term ("borse"), published."""
    taxonomy = TaxonomyFactory.create(code="tags", has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", slug="tags", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="it", slug="etichette", status=PUBLISHED
    )
    bags = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=bags, language="en", slug="bags", status=PUBLISHED)
    TermTranslationFactory.create(term=bags, language="it", slug="borse", status=PUBLISHED)
    return {"taxonomy": taxonomy, "bags": bags}


@pytest.fixture
def bag() -> Product:
    """The replacement product, with an EN and an IT slug."""
    product = ProductFactory.create(sku="BAG-200", legacy_wp_id_en=1001, legacy_wp_id_it=1002)
    ProductTranslationFactory.create(product=product, language="en", slug="leather-bag-en")
    ProductTranslationFactory.create(product=product, language="it", slug="borsa-pelle-it")
    return product


def add(**fields: Any) -> Redirect:
    redirect = Redirect(**fields)
    save_redirect(redirect, ctx=CTX)
    return redirect


# --- Rule 1: a retired SKU's page goes to the replacement product, or to a tag


def test_a_retired_sku_page_moves_to_the_replacement_product(client: Client, bag: Product) -> None:
    add(source_path="/product/old-bag-en-sku-100/", target_kind="product", target_product=bag)
    add(source_path="/it/prodotto/borsa-it-sku-100/", target_kind="product", target_product=bag)
    assert_301(client.get("/product/old-bag-en-sku-100/"), "/product/leather-bag-en/")
    assert_301(client.get("/it/prodotto/borsa-it-sku-100/"), "/it/prodotto/borsa-pelle-it/")


def test_a_retired_sku_page_can_move_to_a_tag_page(client: Client, tags: dict[str, Any]) -> None:
    add(source_path="/product/old-bag-en-sku-101/", target_kind="term", target_term=tags["bags"])
    add(source_path="/it/prodotto/borsa-it-sku-101/", target_kind="term", target_term=tags["bags"])
    assert_301(client.get("/product/old-bag-en-sku-101/"), "/tags/bags/")
    assert_301(client.get("/it/prodotto/borsa-it-sku-101/"), "/it/etichette/borse/")
    assert client.get("/tags/bags/").status_code == 200  # a single hop to a live page


def test_the_query_string_is_kept(client: Client, bag: Product) -> None:
    add(source_path="/product/old-bag-en-sku-100/", target_kind="product", target_product=bag)
    response = client.get("/product/old-bag-en-sku-100/?utm_source=feed")
    assert_301(response, "/product/leather-bag-en/?utm_source=feed")


def test_a_product_without_a_slug_in_the_language_falls_back_to_the_default_one(
    client: Client,
) -> None:
    product = ProductFactory.create()
    ProductTranslationFactory.create(product=product, language="en", slug="only-en-en")
    add(source_path="/de/produkt/alt/", target_kind="product", target_product=product)
    assert_301(client.get("/de/produkt/alt/"), "/product/only-en-en/")


# --- Rule 2: a recently merged tag goes to the surviving tag


def test_a_merged_tag_moves_to_the_surviving_tag(client: Client, tags: dict[str, Any]) -> None:
    add(source_path="/tag-product/handbags/", target_kind="term", target_term=tags["bags"])
    add(
        source_path="/it/tag-product-it/borsette-it/",
        target_kind="term",
        target_term=tags["bags"],
        origin=Redirect.Origin.MERGED_TAG,
    )
    assert_301(client.get("/tag-product/handbags/"), "/tags/bags/")
    assert_301(client.get("/it/tag-product-it/borsette-it/"), "/it/etichette/borse/")


# --- Rule 3: an old discontinued category goes to a search


def test_a_discontinued_category_moves_to_a_search(client: Client) -> None:
    add(source_path="/product-category/old-maps/", target_kind="search", target_query="old maps")
    add(
        source_path="/it/categoria-prodotto/mappe-vecchie/",
        target_kind="search",
        target_query="mappe & carte",
    )
    assert_301(client.get("/product-category/old-maps/"), "/?s=old+maps")
    assert_301(
        client.get("/it/categoria-prodotto/mappe-vecchie/?utm_source=x"), "/it/?s=mappe+%26+carte"
    )


# --- Rule 4: `/author/*` goes to the home page


def test_author_pages_go_home_in_their_language(client: Client) -> None:
    add(source_path="/author/", match_prefix=True, target_kind="path", target_path="/")
    add(source_path="/it/author/", match_prefix=True, target_kind="path", target_path="/it/")
    assert_301(client.get("/author/someone/"), "/")
    assert_301(client.get("/author/someone/page/2/"), "/")
    assert_301(client.get("/it/author/qualcuno/"), "/it/")


def test_an_exact_rule_wins_over_a_prefix_and_the_longest_prefix_wins(client: Client) -> None:
    add(source_path="/author/", match_prefix=True, target_kind="path", target_path="/")
    add(source_path="/author/team/", match_prefix=True, target_kind="path", target_path="/team/")
    add(source_path="/author/team/lead/", target_kind="path", target_path="/about/")
    assert_301(client.get("/author/team/lead/"), "/about/")
    assert_301(client.get("/author/team/other/"), "/team/")
    assert_301(client.get("/author/x/"), "/")


# --- Rule 5: GET `?add-to-cart=` goes to the product page


def test_a_get_add_to_cart_goes_to_the_product_page(client: Client, bag: Product) -> None:
    response = client.get("/?add-to-cart=1001")
    assert response.status_code == 302
    assert response["Location"] == "/product/leather-bag-en/"
    response = client.get("/it/negozio/?add-to-cart=1002&quantity=2")
    assert response.status_code == 302
    assert response["Location"] == "/it/prodotto/borsa-pelle-it/"


def test_an_unknown_add_to_cart_id_only_drops_the_parameter(client: Client) -> None:
    response = client.get("/somewhere/?add-to-cart=999999&utm_source=x")
    assert response.status_code == 302
    assert response["Location"] == "/somewhere/?utm_source=x"
    response = client.get("/?add-to-cart=not-a-number")
    assert response.status_code == 302
    assert response["Location"] == "/"


def test_dropping_add_to_cart_never_leaves_the_site(client: Client) -> None:
    response = client.get("//example.com/?add-to-cart=1")
    assert response.status_code == 302
    assert response["Location"] == "/"


def test_a_post_with_add_to_cart_is_not_redirected(client: Client, bag: Product) -> None:
    assert client.post("/?add-to-cart=1001").status_code != 302


# --- 410, expiry and live pages


def test_a_gone_rule_answers_410(client: Client) -> None:
    add(source_path="/product/withdrawn-en/", code=Redirect.Code.GONE)
    assert client.get("/product/withdrawn-en/").status_code == 410


def test_an_expired_rule_no_longer_applies(client: Client) -> None:
    past = timezone.now() - dt.timedelta(days=1)
    add(source_path="/old/", target_kind="path", target_path="/new/", expires_at=past)
    assert client.get("/old/").status_code == 404
    assert resolve_legacy_path("/old/") is None


@pytest.mark.parametrize("target", ["//evil.example/", "https://evil.example/", "/\\evil"])
def test_an_off_site_target_stored_without_the_services_is_never_followed(
    client: Client, target: str
) -> None:
    """Defence in depth: a row written around `save_redirect` (bulk SQL, a data migration)
    still cannot send a visitor to another site."""
    Redirect.objects.create(source_path="/old/", target_kind="path", target_path=target)
    assert client.get("/old/").status_code == 404


def test_a_page_that_exists_always_wins(client: Client, tags: dict[str, Any]) -> None:
    add(source_path="/tags/bags/", target_kind="path", target_path="/elsewhere/")
    assert client.get("/tags/bags/").status_code == 200


def test_the_rule_matches_without_the_trailing_slash(client: Client) -> None:
    add(source_path="/old/", target_kind="path", target_path="/new/")
    assert resolve_legacy_path("/old") is not None
    assert resolve_legacy_path("/old/") is not None
    assert resolve_legacy_path("/old/x/") is None


def test_an_old_product_slug_keeps_its_cat5_redirect(client: Client, bag: Product) -> None:
    SlugHistoryFactory.create(product=bag, language="en", slug="bag-en-sku-1")
    assert_301(client.get("/product/bag-en-sku-1/"), "/product/leather-bag-en/")


# --- The audited services


def test_saving_a_rule_is_audited_and_dry_run_writes_nothing() -> None:
    redirect = Redirect(source_path="/old/", target_kind="path", target_path="/new/")
    changes = save_redirect(redirect, ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    assert not Redirect.objects.exists()
    assert not entries().exists()

    save_redirect(redirect, ctx=CTX)
    entry = entries().get()
    assert entry.action == "catalog.redirect.created"
    assert entry.target_key == "/old/"
    assert entry.after is not None
    assert entry.after["target_path"] == "/new/"

    redirect.target_path = "/newer/"
    save_redirect(redirect, ctx=CTX)
    update = entries().get(action="catalog.redirect.updated")
    assert update.before is not None
    assert update.after is not None
    assert update.before["target_path"] == "/new/"
    assert update.after["target_path"] == "/newer/"


def test_deleting_a_rule_is_audited() -> None:
    redirect = add(source_path="/old/", target_kind="path", target_path="/new/")
    assert delete_redirect(redirect, ctx=CTX, dry_run=True).summary()["delete"] == 1
    assert Redirect.objects.exists()
    delete_redirect(redirect, ctx=CTX)
    assert not Redirect.objects.exists()
    assert entries().filter(action="catalog.redirect.deleted").count() == 1


@pytest.mark.parametrize(
    "target",
    ["https://example.com/", "//example.com/", "/\\example.com/", "/x/\r\nLocation: /y/"],
)
def test_an_off_site_target_is_refused(target: str) -> None:
    with pytest.raises(ValidationError) as error:
        add(source_path="/old/", target_kind="path", target_path=target)
    assert "target_path" in error.value.message_dict
    assert not Redirect.objects.exists()


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"target_kind": "product"}, "target_product"),
        ({"target_kind": "term"}, "target_term"),
        ({"target_kind": "search"}, "target_query"),
        ({"target_kind": "path"}, "target_path"),
        ({"target_kind": ""}, "target_kind"),
        ({"code": 410, "target_kind": "path", "target_path": "/x/"}, "target_kind"),
        ({"match_prefix": True, "target_kind": "path", "target_path": "/x/"}, "source_path"),
    ],
)
def test_a_rule_names_exactly_its_target(fields: dict[str, Any], field: str) -> None:
    source = "/it/" if fields.get("match_prefix") else "/old/"
    with pytest.raises(ValidationError) as error:
        add(source_path=source, **fields)
    assert field in error.value.message_dict


def test_a_loop_is_refused() -> None:
    with pytest.raises(ValidationError, match="loop"):
        add(source_path="/old/", target_kind="path", target_path="/old/")


def test_a_chain_is_refused_both_ways() -> None:
    add(source_path="/b/", target_kind="path", target_path="/c/")
    with pytest.raises(ValidationError, match="/b/"):
        add(source_path="/a/", target_kind="path", target_path="/b/")
    add(source_path="/x/", target_kind="path", target_path="/y/")
    with pytest.raises(ValidationError, match="/x/"):
        add(source_path="/y/", target_kind="path", target_path="/z/")
    assert Redirect.objects.count() == 2


def test_a_target_on_an_old_product_slug_is_a_chain(bag: Product) -> None:
    SlugHistoryFactory.create(product=bag, language="en", slug="bag-en-sku-1")
    with pytest.raises(ValidationError, match="old product slug"):
        add(source_path="/old/", target_kind="path", target_path="/product/bag-en-sku-1/")


def test_an_expired_rule_does_not_block_a_new_one() -> None:
    past = timezone.now() - dt.timedelta(days=1)
    add(source_path="/b/", target_kind="path", target_path="/c/", expires_at=past)
    add(source_path="/a/", target_kind="path", target_path="/b/")


# --- Importing the existing maps


@pytest.fixture
def maps(bag: Product, tags: dict[str, Any]) -> list[dict[str, str]]:
    vendor = bag.vendor
    return [
        {
            "source_path": "/product/old-bag-en-sku-100/",
            "target_kind": "product",
            "target": f"{vendor.slug}/bag-200",
            "origin": "retired_sku",
        },
        {
            "source_path": "/product/old-bag-en-sku-101/",
            "target_kind": "product",
            "target": bag.staff_ref,
            "origin": "retired_sku",
        },
        {
            "source_path": "/product/old-case-en-sku-102/",
            "target_kind": "term",
            "target": "tags/bags",
            "origin": "retired_sku",
        },
        {
            "source_path": "/it/tag-product-it/borsette-it/",
            "target_kind": "term",
            "target": "tags/borse",
            "origin": "merged_tag",
        },
        {
            "source_path": "/product-category/old-maps/",
            "target_kind": "search",
            "target": "old maps",
            "origin": "discontinued_category",
        },
        {"source_path": "/author/", "match": "prefix", "target_kind": "path", "target": "/"},
        {"source_path": "/product/withdrawn-en/", "code": "410", "expires_at": ""},
    ]


def test_the_maps_import_with_one_audit_entry_per_rule(
    client: Client, maps: list[dict[str, str]], bag: Product, tags: dict[str, Any]
) -> None:
    changes = import_redirects(maps, ctx=CTX)
    assert changes.summary() == {"create": 7, "update": 0, "delete": 0}
    assert entries().filter(action="catalog.redirect.created").count() == 7
    assert Redirect.objects.get(source_path="/product/old-bag-en-sku-100/").target_product == bag
    merged = Redirect.objects.get(source_path="/it/tag-product-it/borsette-it/")
    assert merged.target_term == tags["bags"]
    assert merged.origin == Redirect.Origin.MERGED_TAG
    assert_301(client.get("/it/tag-product-it/borsette-it/"), "/it/etichette/borse/")
    assert client.get("/product/withdrawn-en/").status_code == 410


def test_a_dry_run_import_writes_nothing(maps: list[dict[str, str]]) -> None:
    changes = import_redirects(maps, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 7
    assert not Redirect.objects.exists()
    assert not entries().exists()


def test_a_second_import_changes_only_what_differs(maps: list[dict[str, str]]) -> None:
    import_redirects(maps, ctx=CTX)
    assert import_redirects(maps, ctx=CTX).is_empty
    maps[4]["target"] = "maps"
    changes = import_redirects(maps, ctx=CTX)
    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    assert Redirect.objects.get(source_path="/product-category/old-maps/").target_query == "maps"


def test_a_retired_sku_alias_still_finds_the_product(bag: Product) -> None:
    SkuAliasFactory.create(vendor=bag.vendor, product=bag, sku="BAG-OLD")
    rows = [
        {
            "source_path": "/old/",
            "target_kind": "product",
            "target": f"{bag.vendor.slug}/BAG-OLD",
        }
    ]
    import_redirects(rows, ctx=CTX)
    assert Redirect.objects.get().target_product == bag


def test_an_import_with_errors_writes_nothing_and_names_every_line(
    maps: list[dict[str, str]],
) -> None:
    other = VendorFactory.create()
    maps += [
        {"source_path": "/a/", "target_kind": "product", "target": f"{other.slug}/NOPE"},
        {"source_path": "/b/", "target_kind": "term", "target": "tags/no-such-term"},
        {"source_path": "/c/", "target_kind": "path", "target": "//example.com/"},
        {"source_path": "/d/", "target_kind": "teleport", "target": "/x/"},
        {"source_path": "/product-category/old-maps/", "target_kind": "path", "target": "/x/"},
    ]
    with pytest.raises(ValidationError) as error:
        import_redirects(maps, ctx=CTX)
    text = " ".join(error.value.messages)
    for row in ("row 8", "row 9", "row 10", "row 11", "row 12"):
        assert row in text
    assert not Redirect.objects.exists()
    assert not entries().exists()


def test_an_import_refuses_chains_inside_the_map() -> None:
    rows = [
        {"source_path": "/a/", "target_kind": "path", "target": "/b/"},
        {"source_path": "/b/", "target_kind": "path", "target": "/c/"},
    ]
    with pytest.raises(ValidationError, match="/b/"):
        import_redirects(rows, ctx=CTX)
    assert not Redirect.objects.exists()


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "redirects.csv"
    path.write_text(
        "source_path,target_kind,target,code,match,origin,expires_at,note\n"
        "/author/,path,/,,prefix,legacy_rule,,author archives\n"
        "/product-category/old-maps/,search,old maps,301,,discontinued_category,,\n"
        "/product/withdrawn-en/,,,410,,manual,2030-01-01T00:00:00+00:00,\n",
        encoding="utf-8",
    )
    return path


def test_the_import_command(csv_file: Path) -> None:
    out = StringIO()
    call_command("import_redirects", str(csv_file), "--dry-run", stdout=out)
    assert "Dry run: would create 3" in out.getvalue()
    assert not Redirect.objects.exists()

    out = StringIO()
    call_command("import_redirects", str(csv_file), stdout=out)
    assert "Created 3" in out.getvalue()
    assert Redirect.objects.get(source_path="/author/").match_prefix
    gone = Redirect.objects.get(source_path="/product/withdrawn-en/")
    assert gone.code == Redirect.Code.GONE
    assert gone.expires_at is not None
    assert entries().get(target_key="/author/").actor_label == "command:import_redirects"
