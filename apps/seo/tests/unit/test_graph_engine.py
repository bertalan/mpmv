"""SEO.7: the site-wide graph nodes (pure functions)."""

import pytest

from apps.seo import graph_engine as ge

SITE = "https://shop.example"


def test_operator_node_has_a_stable_id_and_leaves_empty_fields_out() -> None:
    node = ge.operator_node(site=SITE, name="Operator Srl")
    assert node["@id"] == f"{SITE}/#organization"
    assert node["@type"] == "Organization"
    assert set(node) == {"@type", "@id", "name", "legalName", "url"}


def test_operator_node_full() -> None:
    node = ge.operator_node(
        site=SITE,
        name="Operator Srl",
        logo_url=f"{SITE}/logo.svg",
        email="help@shop.example",
        vat_id="IT00000000000",
        profiles=["https://www.wikidata.org/wiki/Q1"],
    )
    assert node["vatID"] == "IT00000000000"
    assert node["contactPoint"]["email"] == "help@shop.example"
    assert node["sameAs"] == ["https://www.wikidata.org/wiki/Q1"]


def test_website_has_a_search_action_only_with_a_search_page() -> None:
    plain = ge.website_node(site=SITE, name="Shop", language="en")
    assert "potentialAction" not in plain
    assert plain["publisher"] == {"@id": f"{SITE}/#organization"}
    with_search = ge.website_node(
        site=SITE,
        name="Shop",
        language="en",
        search_template=f"{SITE}/search/?q={{search_term_string}}",
    )
    assert with_search["potentialAction"]["@type"] == "SearchAction"


def test_webpage_node_links_site_breadcrumb_and_language() -> None:
    node = ge.webpage_node(
        kind="CollectionPage",
        url=f"{SITE}/it/x/",
        site=SITE,
        name="X",
        language="it",
        breadcrumb_id=f"{SITE}/it/x/#breadcrumb",
    )
    assert node["@id"] == f"{SITE}/it/x/#webpage"
    assert node["inLanguage"] == "it"
    assert node["isPartOf"] == {"@id": f"{SITE}/#website"}
    assert node["breadcrumb"] == {"@id": f"{SITE}/it/x/#breadcrumb"}


def test_webpage_node_rejects_an_unknown_type() -> None:
    with pytest.raises(ValueError, match="page type"):
        ge.webpage_node(kind="Thing", url=SITE, site=SITE, name="n", language="en")


def test_compose_keeps_the_first_node_of_an_id_and_drops_inner_contexts() -> None:
    document = ge.compose(
        [
            {"@context": "x", "@id": "a", "name": "first"},
            {"@id": "a", "name": "second"},
            {},
            {"@type": "Thing"},
        ]
    )
    assert document["@context"] == "https://schema.org"
    assert document["@graph"] == [{"@id": "a", "name": "first"}, {"@type": "Thing"}]


def test_faq_node_is_empty_without_questions() -> None:
    assert ge.faq_node(url=SITE, language="en", questions=[]) == {}
    node = ge.faq_node(url=SITE, language="en", questions=[("Q?", "<p>A</p>")])
    assert node["@type"] == "FAQPage"
    assert node["mainEntity"][0]["acceptedAnswer"]["text"] == "<p>A</p>"
