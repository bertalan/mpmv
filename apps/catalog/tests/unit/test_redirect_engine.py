"""CAT.6: the pure redirect rules (spec 03 §3.1-3.2): safe paths, matching, loops and chains.

A redirect never leaves the site: sources and targets are same-site paths, so absolute URLs,
scheme-relative `//host`, backslashes and control characters (CR/LF) are refused.
"""

import pytest

from apps.catalog.engine import (
    RedirectRule,
    normalise_redirect_source,
    path_language,
    prefix_source_error,
    redirect_path_error,
    redirect_problems,
    rule_matches,
    with_query,
)

LANGUAGES = ("en", "it", "de")


# --- Safe paths: no open redirect


@pytest.mark.parametrize(
    "path",
    [
        "/old-page/",
        "/product/bag-en-sku-1/",
        "/it/tag-product-it/borsa-it/",
        "/author/someone/",
        "/café/",
    ],
)
def test_same_site_paths_are_accepted(path: str) -> None:
    assert redirect_path_error(path, allow_query=False) is None
    assert redirect_path_error(path, allow_query=True) is None


@pytest.mark.parametrize(
    "path",
    [
        "",
        "old-page/",
        "https://example.com/",
        "http:/example.com/",
        "javascript:alert(1)",
        "//example.com/",
        "///example.com/",
        "/\\example.com/",
        "\\\\example.com/",
        "/ok/\r\nSet-Cookie: x=1",
        "/ok/\n",
        "/with space/",
        "/tab\there/",
        "/nul\x00/",
        "/../etc/",
        "/a/./b/",
        "/" + "a" * 600,
    ],
)
def test_unsafe_or_off_site_paths_are_refused(path: str) -> None:
    assert redirect_path_error(path, allow_query=True) is not None


def test_a_source_has_no_query_or_fragment_but_a_target_may_have_a_query() -> None:
    assert redirect_path_error("/old/?a=1", allow_query=False) is not None
    assert redirect_path_error("/old/#top", allow_query=False) is not None
    assert redirect_path_error("/new/?a=1", allow_query=True) is None
    assert redirect_path_error("/new/?next=//evil.example", allow_query=True) is None


@pytest.mark.parametrize("path", ["/", "/it/", "/de/", "/author"])
def test_a_prefix_never_covers_a_whole_site_and_ends_with_a_slash(path: str) -> None:
    assert prefix_source_error(path, languages=LANGUAGES) is not None


@pytest.mark.parametrize("path", ["/author/", "/it/author/", "/old-blog/2019/"])
def test_a_prefix_below_a_language_root_is_accepted(path: str) -> None:
    assert prefix_source_error(path, languages=LANGUAGES) is None


@pytest.mark.parametrize(
    "path",
    [
        "/admin/",
        "/admin/pages/",
        "/django-admin/",
        "/api/",
        "/api/v1/products/",
        "/media/",
        "/media/original_images/",
        "/static/",
        "/manager/",
        "/manager/shop/",
        "/account/",
        "/account/mfa/",
        "/login/",
        "/webhooks/",
    ],
)
def test_a_prefix_never_covers_the_admin_api_assets_manager_or_login(path: str) -> None:
    """A prefix rule there would turn JSON, asset and sign-in 404s into HTML redirects."""
    assert prefix_source_error(path, languages=LANGUAGES) is not None


@pytest.mark.parametrize("path", ["/administration-old/", "/apis-blog/", "/static-pages/"])
def test_a_prefix_that_only_starts_like_a_reserved_one_is_accepted(path: str) -> None:
    assert prefix_source_error(path, languages=LANGUAGES) is None


# --- Percent-encoded sources (legacy maps): stored decoded, as Django decodes request.path


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("/caf%C3%A9/", "/café/"),
        ("/caf%c3%a9/", "/café/"),
        ("/café/", "/café/"),
        ("/old-page/", "/old-page/"),
        ("/a%2Fb/", "/a/b/"),
    ],
)
def test_a_source_is_stored_decoded(raw: str, stored: str) -> None:
    assert normalise_redirect_source(raw) == stored


def test_decoding_is_idempotent_on_a_valid_source() -> None:
    once = normalise_redirect_source("/caf%C3%A9/")
    assert normalise_redirect_source(once) == once


@pytest.mark.parametrize(
    "raw",
    [
        "/%2F%2Fevil.example/",  # decodes to //evil.example/
        "/a%0D%0ASet-Cookie:x/",  # CR/LF
        "/a%00b/",  # NUL
        "/%2E%2E/etc/",  # dot segment
        "/a%3Fq=1/",  # a query once decoded
        "/a%5Cb/",  # backslash
        "/a%FFb/",  # not UTF-8
        "/a%2525b/",  # still holds "%" once decoded: double encoding
        "/100%/",  # a stray "%"
    ],
)
def test_a_source_that_is_unsafe_once_decoded_is_refused(raw: str) -> None:
    decoded = normalise_redirect_source(raw)
    assert redirect_path_error(decoded, allow_query=False) is not None


# --- Language of a path


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("/tag-product/bag/", "en"),
        ("/it/tag-product-it/borsa-it/", "it"),
        ("/de/", "de"),
        ("/en/x/", "en"),
        ("/item/", "en"),
        ("/its/", "en"),
    ],
)
def test_the_language_of_a_path_is_its_prefix(path: str, language: str) -> None:
    assert path_language(path, languages=LANGUAGES, default="en") == language


# --- Matching


def test_an_exact_rule_matches_with_or_without_the_trailing_slash() -> None:
    assert rule_matches("/old/", prefix=False, path="/old/")
    assert rule_matches("/old/", prefix=False, path="/old")
    assert not rule_matches("/old/", prefix=False, path="/old/more/")
    assert not rule_matches("/old/", prefix=False, path="/older/")


def test_a_prefix_rule_matches_everything_below_it() -> None:
    assert rule_matches("/author/", prefix=True, path="/author/someone/")
    assert rule_matches("/author/", prefix=True, path="/author/someone/page/2/")
    assert rule_matches("/author/", prefix=True, path="/author")
    assert not rule_matches("/author/", prefix=True, path="/authors/")


def test_the_request_query_is_kept_unless_the_target_has_one() -> None:
    assert with_query("/new/", "utm_source=feed") == "/new/?utm_source=feed"
    assert with_query("/new/", "") == "/new/"
    assert with_query("/?s=bag", "utm_source=feed") == "/?s=bag"


# --- Loops and chains


def rule(source: str, target: str | None, *, prefix: bool = False) -> RedirectRule:
    return RedirectRule(source=source, prefix=prefix, target=target)


def test_a_rule_to_itself_is_a_loop() -> None:
    problems = redirect_problems([rule("/old/", "/old/")], checked={"/old/"})
    assert "loop" in problems["/old/"]


def test_a_prefix_rule_whose_target_is_below_it_is_a_loop() -> None:
    problems = redirect_problems(
        [rule("/author/", "/author/x/", prefix=True)], checked={"/author/"}
    )
    assert "loop" in problems["/author/"]


def test_a_rule_to_another_rule_is_a_chain() -> None:
    rules = [rule("/a/", "/b/"), rule("/b/", "/c/")]
    problems = redirect_problems(rules, checked={"/a/"})
    assert "/b/" in problems["/a/"]
    assert set(problems) == {"/a/"}


def test_a_new_rule_on_an_existing_target_is_a_chain_too() -> None:
    rules = [rule("/a/", "/b/"), rule("/b/", "/c/")]
    problems = redirect_problems(rules, checked={"/b/"})
    assert "/a/" in problems["/b/"]


def test_a_two_rule_cycle_is_reported_on_both_rules() -> None:
    rules = [rule("/a/", "/b/"), rule("/b/", "/a/")]
    assert set(redirect_problems(rules, checked={"/a/", "/b/"})) == {"/a/", "/b/"}


def test_a_target_under_a_prefix_rule_is_a_chain() -> None:
    rules = [rule("/old/", "/author/x/"), rule("/author/", "/", prefix=True)]
    assert "/author/" in redirect_problems(rules, checked={"/old/"})["/old/"]


def test_the_query_of_a_target_does_not_hide_a_chain() -> None:
    rules = [rule("/a/", "/b/?x=1"), rule("/b/", "/c/")]
    assert "/a/" in redirect_problems(rules, checked={"/a/"})


def test_gone_rules_and_independent_rules_are_fine() -> None:
    rules = [rule("/a/", "/c/"), rule("/b/", "/c/"), rule("/gone/", None)]
    assert redirect_problems(rules, checked={"/a/", "/b/", "/gone/"}) == {}


def test_a_target_that_is_a_gone_rule_is_a_chain() -> None:
    rules = [rule("/a/", "/gone/"), rule("/gone/", None)]
    assert "/gone/" in redirect_problems(rules, checked={"/a/"})["/a/"]


@pytest.mark.parametrize(
    "path",
    [
        "/it/vendor/apply/",
        "/de/vendor/apply/confirm/x/",
        "/it/vendor/apply/sent/",
    ],
)
def test_a_reserved_path_stays_reserved_under_a_language_prefix(path: str) -> None:
    """The "Sell with us" pages exist in every language (I18N.11): `/it/vendor/apply/` is as
    reserved as `/vendor/apply/`."""
    assert prefix_source_error(path, languages=LANGUAGES) is not None


@pytest.mark.parametrize("path", ["/it/vendor/", "/it/vendors-old/", "/fr/vendor/apply/"])
def test_a_language_prefix_does_not_reserve_more_than_the_path_below_it(path: str) -> None:
    """`/fr/` is not a configured language here, so it is an ordinary first segment."""
    assert prefix_source_error(path, languages=LANGUAGES) is None


@pytest.mark.parametrize("path", ["/it/media/", "/it/static/x/", "/de/api/", "/it/admin/"])
def test_the_other_reserved_roots_match_only_unprefixed(path: str) -> None:
    """Files, API and admins have no language prefix: below `/it/` they are ordinary paths."""
    assert prefix_source_error(path, languages=LANGUAGES) is None
