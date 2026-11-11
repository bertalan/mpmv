"""SEO.6 (1/n): the pure rules of the anonymous page cache."""

import datetime as dt

import pytest

from apps.seo import page_cache_engine as engine

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.UTC)
COOKIES = frozenset({"sessionid", "csrftoken", "messages"})


def parts(**override: object) -> engine.KeyParts:
    base: dict[str, object] = {
        "scheme": "https",
        "host": "shop.example",
        "path": "/about/",
        "language": "en",
        "noindex": False,
        "generation": "g1",
    }
    return engine.KeyParts(**{**base, **override})  # type: ignore[arg-type]


# --- the key varies by everything that changes the HTML


@pytest.mark.parametrize(
    "change",
    [
        {"scheme": "http"},
        {"host": "other.example"},
        {"path": "/about/team/"},
        {"language": "it"},
        {"noindex": True},
        {"generation": "g2"},
    ],
)
def test_the_key_varies_by_scheme_host_path_language_noindex_and_generation(
    change: dict[str, object],
) -> None:
    assert engine.cache_key(parts()) != engine.cache_key(parts(**change))


def test_the_same_parts_give_the_same_key() -> None:
    assert engine.cache_key(parts()) == engine.cache_key(parts())


def test_the_key_is_safe_for_every_backend() -> None:
    key = engine.cache_key(parts(path="/a b/é\n/"))
    assert key.isascii()
    assert len(key) < 100
    assert not any(ch.isspace() for ch in key)


# --- which requests are never served from the cache


def bypass(**override: object) -> str | None:
    base: dict[str, object] = {
        "method": "GET",
        "query_string": "",
        "path": "/about/",
        "cookie_names": frozenset(),
        "bypass_cookies": COOKIES,
        "authorization": False,
    }
    return engine.request_bypass_reason(**{**base, **override})  # type: ignore[arg-type]


def test_an_anonymous_get_without_cookies_is_cacheable() -> None:
    assert bypass() is None


@pytest.mark.parametrize(
    "change",
    [
        {"method": "POST"},
        {"method": "HEAD"},
        {"query_string": "utm_source=x"},
        {"query_string": "wagtail_preview=1"},
        {"cookie_names": frozenset({"sessionid"})},
        {"cookie_names": frozenset({"csrftoken", "other"})},
        {"cookie_names": frozenset({"messages"})},
        {"authorization": True},
        {"path": "/admin/pages/3/view_draft/"},
        {"path": "/manager/"},
        {"path": "/account/mfa/"},
        {"path": "/api/v1/products/"},
        {"path": "/django-admin/"},
        {"path": "/i18n/switch/"},
    ],
)
def test_these_requests_bypass_the_cache(change: dict[str, object]) -> None:
    assert bypass(**change)


def test_an_unrelated_cookie_does_not_bypass_the_cache() -> None:
    assert bypass(cookie_names=frozenset({"theme_hint"})) is None


# --- which responses are never stored


def skip(**override: object) -> str | None:
    base: dict[str, object] = {
        "status": 200,
        "content_type": "text/html; charset=utf-8",
        "streaming": False,
        "sets_cookie": False,
        "cache_control": "",
        "session_modified": False,
        "nonce_used": False,
        "csrf_used": False,
        "body": b"<html><body>Hello</body></html>",
        "max_bytes": 1024,
    }
    return engine.response_skip_reason(**{**base, **override})  # type: ignore[arg-type]


def test_a_plain_html_page_is_stored() -> None:
    assert skip() is None


@pytest.mark.parametrize(
    "change",
    [
        {"status": 404},
        {"status": 302},
        {"content_type": "application/json"},
        {"streaming": True},
        {"sets_cookie": True},
        {"cache_control": "private"},
        {"cache_control": "no-store"},
        {"cache_control": "no-cache"},
        {"cache_control": "max-age=0"},
        {"session_modified": True},
        {"nonce_used": True},
        {"csrf_used": True},
        {"body": b'<form><input name="csrfmiddlewaretoken" value="x"></form>'},
        {"body": b'<script nonce="abc123">1</script>'},
        {"body": b"x" * 2048},
    ],
)
def test_these_responses_are_not_stored(change: dict[str, object]) -> None:
    assert skip(**change)


def test_a_public_response_with_a_lifetime_is_stored() -> None:
    assert skip(cache_control="public, max-age=60") is None


# --- how long a stored page lives


def test_the_default_lifetime_applies_without_headers() -> None:
    assert engine.lifetime(300, "", b"<p>x</p>", NOW) == 300


def test_a_shorter_max_age_or_s_maxage_wins() -> None:
    assert engine.lifetime(300, "public, max-age=60", b"", NOW) == 60
    assert engine.lifetime(300, "public, s-maxage=30, max-age=90", b"", NOW) == 30


def test_a_longer_max_age_does_not_extend_the_lifetime() -> None:
    assert engine.lifetime(300, "max-age=86400", b"", NOW) == 300


def countdown(target: dt.datetime) -> bytes:
    stamp = target.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'<div data-countdown data-target="{stamp}">x</div>'.encode()


def test_an_active_countdown_caps_the_lifetime_at_its_target() -> None:
    body = countdown(NOW + dt.timedelta(seconds=90))
    assert engine.lifetime(300, "", body, NOW) == 90


def test_the_nearest_of_several_countdowns_wins() -> None:
    body = countdown(NOW + dt.timedelta(seconds=200)) + countdown(NOW + dt.timedelta(seconds=45))
    assert engine.lifetime(300, "", body, NOW) == 45


def test_a_countdown_further_away_than_the_default_does_not_extend_it() -> None:
    assert engine.lifetime(300, "", countdown(NOW + dt.timedelta(days=3)), NOW) == 300


def test_a_countdown_already_reached_does_not_count() -> None:
    assert engine.lifetime(300, "", countdown(NOW - dt.timedelta(hours=1)), NOW) == 300


def test_a_countdown_under_a_second_away_is_not_stored() -> None:
    body = countdown(NOW + dt.timedelta(seconds=1))
    assert engine.lifetime(300, "", body, NOW + dt.timedelta(milliseconds=600)) == 0


def test_a_malformed_target_is_ignored() -> None:
    assert engine.lifetime(300, "", b'<div data-countdown data-target="soon">', NOW) == 300


def test_a_zero_max_age_means_not_stored() -> None:
    assert engine.lifetime(300, "max-age=0", b"", NOW) == 0
