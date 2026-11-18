"""SEO.7 part 2: the `ItemList` node and the search action template (pure functions)."""

from apps.seo import graph_engine as ge

SITE = "https://shop.example"


def entries() -> list[ge.ListEntry]:
    return [
        ge.ListEntry(url=f"{SITE}/product/a/", name="A", kind="Product", image=f"{SITE}/a.jpg"),
        ge.ListEntry(url=f"{SITE}/events/b/", name="B"),
    ]


def test_item_list_has_ordered_list_items_that_reference_page_ids() -> None:
    node = ge.item_list_node(
        list_id=f"{SITE}/x/#items", name="Things", language="en", entries=entries()
    )
    assert node["@type"] == "ItemList"
    assert node["@id"] == f"{SITE}/x/#items"
    assert node["inLanguage"] == "en"
    assert node["numberOfItems"] == 2
    first, second = node["itemListElement"]
    assert (first["@type"], first["position"]) == ("ListItem", 1)
    assert first["item"] == {
        "@id": f"{SITE}/product/a/",
        "@type": "Product",
        "name": "A",
        "url": f"{SITE}/product/a/",
        "image": f"{SITE}/a.jpg",
    }
    assert second["position"] == 2
    assert "@type" not in second["item"]
    assert "image" not in second["item"]


def test_an_empty_list_gives_no_node() -> None:
    assert ge.item_list_node(list_id="x", name="n", language="en", entries=[]) == {}


def test_a_page_listed_twice_stays_once() -> None:
    node = ge.item_list_node(
        list_id="x", name="n", language="en", entries=[*entries(), entries()[0]]
    )
    assert node["numberOfItems"] == 2


def test_search_template_keeps_the_placeholder_unescaped() -> None:
    assert ge.search_template(SITE, "/") == f"{SITE}/?s={{search_term_string}}"
    assert ge.search_template(SITE, "/it/") == f"{SITE}/it/?s={{search_term_string}}"
