"""EVT.14: the pure rules of the tile proxy (validation, URL, cache lifetime, cache blob)."""

import datetime as dt

import pytest

from apps.core.maps import TileKey, engine

NOW = dt.datetime(2027, 3, 1, 12, 0, tzinfo=dt.UTC)
PNG = engine.PNG_MAGIC + b"x"
TEMPLATE = "https://tiles.example.eu/{z}/{x}/{y}.png"


def _parse(z: str, x: str, y: str) -> TileKey | None:
    return engine.parse_tile(z, x, y, min_zoom=0, max_zoom=18)


@pytest.mark.parametrize(
    ("z", "x", "y"), [("0", "0", "0"), ("18", "262143", "262143"), ("3", "7", "0"), ("2", "3", "3")]
)
def test_valid_tiles_parse(z: str, x: str, y: str) -> None:
    assert _parse(z, x, y) == TileKey(int(z), int(x), int(y))


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [
        ("19", "0", "0"),  # above the maximum zoom
        ("-1", "0", "0"),
        ("0", "-1", "0"),
        ("1", "0", "2"),  # y out of range for z=1
        ("1", "2", "0"),
        ("a", "0", "0"),
        ("1", "1.5", "0"),
        ("1", "0", "0x1"),
        ("+1", "0", "0"),
        ("01", "0", "0"),  # not canonical
        ("1", " 1", "0"),
        ("\u0661", "0", "0"),  # Arabic-Indic digit: not ASCII
        ("", "0", "0"),
        ("1", "0", "9" * 40),
    ],
)
def test_invalid_tiles_are_refused(z: str, x: str, y: str) -> None:
    assert _parse(z, x, y) is None


def test_the_zoom_range_comes_from_the_arguments() -> None:
    assert engine.parse_tile("19", "0", "0", min_zoom=0, max_zoom=19) is not None
    assert engine.parse_tile("1", "0", "0", min_zoom=2, max_zoom=18) is None


def test_template_rules() -> None:
    assert engine.validate_template(TEMPLATE) is None
    assert engine.validate_template("https://t.example.eu/{z}/{x}.png")
    assert engine.validate_template("http://t.example.eu/{z}/{x}/{y}.png")
    assert engine.upstream_url(TEMPLATE, TileKey(3, 4, 5)) == "https://tiles.example.eu/3/4/5.png"


def test_the_cache_path_depends_on_the_upstream_and_never_shows_it() -> None:
    key = TileKey(3, 4, 5)
    keyed = "https://tiles.example.eu/{z}/{x}/{y}.png?apikey=not-a-secret-value"
    first, other = engine.cache_path(TEMPLATE, key), engine.cache_path(keyed, key)
    assert first != other
    assert "apikey" not in other
    assert first.endswith("/3/4/5.tile")


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, engine.MIN_CACHE_SECONDS),
        ({"Cache-Control": "max-age=3600"}, engine.MIN_CACHE_SECONDS),  # shorter: 7 days wins
        ({"Cache-Control": "public, max-age=2592000"}, 2592000),
        ({"cache-control": "max-age=99999999999"}, engine.MAX_CACHE_SECONDS),
        ({"Cache-Control": "no-store", "Expires": "Wed, 01 Mar 2028 12:00:00 GMT"}, 31536000),
        ({"Expires": "Wed, 31 Mar 2027 12:00:00 GMT"}, 30 * 24 * 3600),
        ({"Expires": "garbage"}, engine.MIN_CACHE_SECONDS),
    ],
)
def test_cache_lifetime(headers: dict[str, str], expected: int) -> None:
    assert engine.cache_seconds(headers, NOW) == expected


def test_no_store_without_expires_uses_the_minimum() -> None:
    assert engine.cache_seconds({"Cache-Control": "no-store"}, NOW) == engine.MIN_CACHE_SECONDS


def test_the_blob_round_trips_and_rejects_foreign_data() -> None:
    expires = NOW + dt.timedelta(days=7)
    assert engine.unpack(engine.pack(PNG, expires)) == (PNG, expires)
    assert engine.unpack(b"no header") is None
    assert engine.unpack(b'{"expires": "nope"}\n' + PNG) is None
    assert engine.unpack(b'{"expires": "2027-03-01T12:00:00"}\n' + PNG) is None  # naive
    assert engine.unpack(engine.pack(b"<html>", expires)) is None
