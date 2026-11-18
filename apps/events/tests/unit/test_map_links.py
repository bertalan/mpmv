"""EVT.15: the directions and search links of an event (spec 15 "Maps"), pure functions."""

from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from apps.events import engine


def test_directions_links_for_a_point() -> None:
    links = engine.directions_links(Decimal("45.464204"), Decimal("9.189982"))
    assert links.google == "https://www.google.com/maps/dir/?api=1&destination=45.464204,9.189982"
    assert links.geo == "geo:45.464204,9.189982"
    parts = urlsplit(links.osm)
    assert (
        f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.openstreetmap.org/directions"
    )
    assert parse_qs(parts.query)["route"] == [";45.464204,9.189982"]


def test_negative_coordinates_and_trailing_zeros() -> None:
    links = engine.directions_links(Decimal("-33.500000"), Decimal("-70.250000"))
    assert links.geo == "geo:-33.5,-70.25"
    assert links.google.endswith("destination=-33.5,-70.25")


def test_search_link_joins_the_non_empty_parts() -> None:
    url = engine.search_url(["Fiera Esempio", "Via Esempio 1", "", "20121 Milano", "IT"])
    assert url is not None
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.openstreetmap.org/search"
    assert parse_qs(parts.query)["query"] == ["Fiera Esempio, Via Esempio 1, 20121 Milano, IT"]


def test_search_link_without_an_address_is_none() -> None:
    assert engine.search_url([]) is None
    assert engine.search_url(["", "  "]) is None


def test_search_link_encodes_the_query() -> None:
    url = engine.search_url(["A&B <x> street"])
    assert url is not None
    assert "<" not in url
    assert "&B" not in url.split("?", 1)[1].replace("%26B", "")
