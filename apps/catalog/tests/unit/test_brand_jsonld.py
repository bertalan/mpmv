"""CAT.20 (3/3): the schema.org nodes of a brand and its manufacturer (pure rules, no database).

They are graph nodes with stable `@id`s (spec 10, SEO.7 conventions): the same brand gives the
same node for every vendor and language page, links are https only, and a missing value is left
out, never guessed.
"""

import json

from apps.catalog.engine import (
    brand_node,
    brand_node_id,
    jsonld_text,
    manufacturer_node,
    plain_description,
)

SITE = "https://shop.example"


def test_the_node_id_is_the_source_language_page_plus_the_brand_fragment() -> None:
    paths = {"it": "/it/marchio/acme-it/", "en": "/brand/acme/"}
    assert brand_node_id(paths, source="en", site=SITE) == f"{SITE}/brand/acme/#brand"


def test_the_node_id_does_not_depend_on_the_page_language() -> None:
    paths = {"it": "/it/marchio/acme-it/", "de": "/de/brand/acme-de/"}
    # No source-language page: the first language in alphabetical order, whatever page asks.
    assert brand_node_id(paths, source="en", site=SITE) == f"{SITE}/de/brand/acme-de/#brand"


def test_a_brand_without_a_public_page_has_no_node_id() -> None:
    assert brand_node_id({}, source="en", site=SITE) == ""


def test_the_brand_node_carries_name_url_logo_description_and_same_as() -> None:
    node = brand_node(
        node_id=f"{SITE}/brand/acme/#brand",
        name="Acme",
        website="https://acme.example/",
        logo_url=f"{SITE}/media/logo.png",
        description="Anvils since 1949.",
        wikidata_id="Q42",
        same_as=["https://en.wikipedia.org/wiki/Acme"],
        manufacturer_id=f"{SITE}/#manufacturer-1",
    )
    assert node == {
        "@type": "Brand",
        "@id": f"{SITE}/brand/acme/#brand",
        "name": "Acme",
        "url": "https://acme.example/",
        "logo": f"{SITE}/media/logo.png",
        "description": "Anvils since 1949.",
        "sameAs": ["https://www.wikidata.org/wiki/Q42", "https://en.wikipedia.org/wiki/Acme"],
    }


def test_empty_values_are_left_out() -> None:
    node = brand_node(node_id=f"{SITE}/brand/acme/#brand", name="Acme")
    assert node == {"@type": "Brand", "@id": f"{SITE}/brand/acme/#brand", "name": "Acme"}


def test_only_https_links_reach_the_same_as_list_and_each_once() -> None:
    node = brand_node(
        node_id="x",
        name="Acme",
        same_as=[
            "http://insecure.example/",
            "javascript:alert(1)",
            "https://ok.example/a",
            "https://ok.example/a",
            "https://user:secret@bad.example/",  # credentials: refused
            "https://has space.example/",
            42,  # type: ignore[list-item]
        ],
    )
    assert node["sameAs"] == ["https://ok.example/a"]


def test_a_non_https_official_url_is_dropped() -> None:
    node = brand_node(node_id="x", name="Acme", website="http://acme.example/")
    assert "url" not in node


def test_an_invalid_wikidata_id_adds_no_link() -> None:
    node = brand_node(node_id="x", name="Acme", wikidata_id="Q0; drop")
    assert "sameAs" not in node


def test_the_manufacturer_is_an_organization_that_points_back_at_the_brand() -> None:
    node = manufacturer_node(
        node_id=f"{SITE}/#manufacturer-1",
        legal_name="Acme Holdings Ltd",
        url="https://holdings.example/",
        same_as=["https://www.wikidata.org/wiki/Q7"],
        country="GB",
        brand_ids=[f"{SITE}/brand/acme/#brand"],
    )
    assert node == {
        "@type": "Organization",
        "@id": f"{SITE}/#manufacturer-1",
        "name": "Acme Holdings Ltd",
        "legalName": "Acme Holdings Ltd",
        "url": "https://holdings.example/",
        "sameAs": ["https://www.wikidata.org/wiki/Q7"],
        "address": {"@type": "PostalAddress", "addressCountry": "GB"},
        "brand": [{"@id": f"{SITE}/brand/acme/#brand"}],
    }


def test_a_brand_node_can_point_at_its_manufacturer() -> None:
    node = brand_node(node_id="b", name="Acme", manufacturer_id="m")
    assert node["manufacturer"] == {"@id": "m"}


def test_the_description_is_plain_text_without_markup_or_entities() -> None:
    html = "<p>Anvils &amp; <b>hammers</b></p><script>alert(1)</script><p>since 1949</p>"
    assert plain_description(html) == "Anvils & hammers since 1949"


LINE_SEPARATOR = "\N{LINE SEPARATOR}"


def test_jsonld_text_cannot_leave_its_script_element() -> None:
    name = f"</script><img src=x onerror=alert(1)>&{LINE_SEPARATOR}"
    text = jsonld_text({"name": name})
    assert "<" not in text
    assert ">" not in text
    assert "&" not in text
    assert LINE_SEPARATOR not in text
    assert json.loads(text)["name"] == name
