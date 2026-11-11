"""SEO.5 and SEO.9: the consent endpoint, the log, the provider setting and the gating tags."""

import re
import uuid
from collections.abc import Iterator

import pytest
from django.conf import settings as django_settings
from django.core.checks import Error, Warning
from django.template import Context, Template
from django.test import Client, RequestFactory, override_settings

from apps.seo import engine
from apps.seo.checks import check_consent_provider
from apps.seo.models import ConsentLog
from apps.seo.providers import SelfHostedConsentProvider, get_provider
from apps.seo.tests.factories import ConsentLogFactory
from apps.seo.tests.fakes import FakeConsentProvider

pytestmark = pytest.mark.django_db

FAKE = {
    "MPMV_CONSENT_PROVIDER": "fake",
    "MPMV_CONSENT_PROVIDERS": {"fake": "apps.seo.tests.fakes.FakeConsentProvider"},
}
SAME_SITE = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _reset_fake() -> Iterator[None]:
    FakeConsentProvider.granted = set()
    FakeConsentProvider.recorded = []
    yield
    FakeConsentProvider.granted = set()
    FakeConsentProvider.recorded = []


def cookie_of(client: Client) -> str:
    return client.cookies[engine.COOKIE_NAME].value


# --- The endpoint


def test_accept_all_logs_the_choice_and_sets_the_cookie(client: Client) -> None:
    response = client.post("/consent/", {"action": "accept_all"}, headers=SAME_SITE)
    assert response.status_code == 303
    state = engine.parse_cookie(cookie_of(client))
    assert state.decided
    assert state.granted == frozenset(engine.GATED_CATEGORIES)
    log = ConsentLog.objects.get()
    assert log.consent_id == uuid.UUID(state.consent_id)
    assert log.action == engine.ACCEPT_ALL
    assert log.granted == list(engine.GATED_CATEGORIES)
    assert log.banner_version == engine.BANNER_VERSION
    assert log.provider == "self-hosted"


def test_reject_all_grants_nothing_and_is_logged_too(client: Client) -> None:
    client.post(
        "/consent/", {"action": "reject_all", "categories": ["marketing"]}, headers=SAME_SITE
    )
    state = engine.parse_cookie(cookie_of(client))
    assert state.decided
    assert state.granted == frozenset()
    assert ConsentLog.objects.get().granted == []


def test_a_custom_choice_grants_the_ticked_categories(client: Client) -> None:
    client.post(
        "/consent/", {"action": "custom", "categories": ["statistics", "bogus"]}, headers=SAME_SITE
    )
    assert engine.parse_cookie(cookie_of(client)).granted == frozenset({engine.STATISTICS})
    assert ConsentLog.objects.get().granted == ["statistics"]


def test_a_later_change_is_a_new_row_with_the_same_consent_id(client: Client) -> None:
    client.post("/consent/", {"action": "accept_all"}, headers=SAME_SITE)
    client.post("/consent/", {"action": "reject_all"}, headers=SAME_SITE)
    first, second = ConsentLog.objects.order_by("id")
    assert first.consent_id == second.consent_id
    assert (first.action, second.action) == (engine.ACCEPT_ALL, engine.REJECT_ALL)
    assert not engine.parse_cookie(cookie_of(client)).granted


def test_the_log_holds_no_more_than_the_proof(client: Client) -> None:
    client.post("/consent/", {"action": "accept_all"}, REMOTE_ADDR="203.0.113.9", headers=SAME_SITE)
    columns = {f.name for f in ConsentLog._meta.get_fields()}
    assert columns == {
        "id", "consent_id", "action", "granted", "banner_version", "provider", "language",
        "decided_at",
    }  # fmt: skip
    assert "203.0.113.9" not in str(ConsentLog.objects.values().get())


def test_a_log_row_is_never_changed() -> None:
    row = ConsentLogFactory.create()
    row.action = engine.ACCEPT_ALL
    with pytest.raises(ValueError, match="never changed"):
        row.save()


def test_the_cookie_is_readable_by_the_page_script_and_lasts_six_months(client: Client) -> None:
    client.post("/consent/", {"action": "accept_all"}, headers=SAME_SITE)
    morsel = client.cookies[engine.COOKIE_NAME]
    assert not morsel["httponly"]
    assert morsel["samesite"] == "Lax"
    assert morsel["max-age"] == engine.COOKIE_MAX_AGE
    assert morsel["path"] == "/"


def test_a_script_gets_json_and_a_form_gets_a_redirect_back(client: Client) -> None:
    response = client.post(
        "/consent/", {"action": "accept_all"}, headers={**SAME_SITE, "Accept": "application/json"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["granted"] == sorted(engine.GATED_CATEGORIES)
    assert body["signals"]["analytics_storage"] == "granted"
    back = client.post(
        "/consent/",
        {"action": "reject_all"},
        headers={**SAME_SITE, "Referer": "http://testserver/blog/"},
    )
    assert (back.status_code, back["Location"]) == (303, "http://testserver/blog/")
    assert back["Cache-Control"] == "no-store"


def test_a_redirect_never_leaves_the_site(client: Client) -> None:
    response = client.post(
        "/consent/", {"action": "reject_all", "next": "https://evil.example/x"}, headers=SAME_SITE
    )
    assert response["Location"] == "/"


def test_a_cross_site_post_cannot_give_or_withdraw_consent(client: Client) -> None:
    for headers in (
        {},
        {"Origin": "https://evil.example"},
        {"Referer": "https://evil.example/"},
    ):
        response = client.post("/consent/", {"action": "accept_all"}, headers=headers)
        assert response.status_code == 403
    assert not ConsentLog.objects.exists()
    assert engine.COOKIE_NAME not in client.cookies


def test_the_referer_stands_in_when_there_is_no_origin(client: Client) -> None:
    response = client.post(
        "/consent/", {"action": "accept_all"}, headers={"Referer": "http://testserver/about/"}
    )
    assert response.status_code == 303


def test_an_unknown_action_is_refused(client: Client) -> None:
    assert client.post("/consent/", {"action": "yes please"}, headers=SAME_SITE).status_code == 400
    assert not ConsentLog.objects.exists()


def test_get_is_not_allowed(client: Client) -> None:
    assert client.get("/consent/").status_code == 405


def test_the_endpoint_is_rate_limited_per_ip(client: Client) -> None:
    from apps.seo.views import CHOICE_LIMIT

    for _ in range(CHOICE_LIMIT):
        assert (
            client.post("/consent/", {"action": "reject_all"}, headers=SAME_SITE).status_code == 303
        )
    limited = client.post("/consent/", {"action": "reject_all"}, headers=SAME_SITE)
    assert limited.status_code == 429
    assert int(limited["Retry-After"]) >= 1
    other = client.post(
        "/consent/", {"action": "reject_all"}, REMOTE_ADDR="198.51.100.5", headers=SAME_SITE
    )
    assert other.status_code == 303


# --- The settings page


def test_the_settings_page_shows_the_current_choice_and_is_not_cached(client: Client) -> None:
    client.post("/consent/", {"action": "custom", "categories": ["statistics"]}, headers=SAME_SITE)
    response = client.get("/cookie-settings/")
    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(r'id="prefs-statistics"[^>]*\schecked', html)
    assert not re.search(r'id="prefs-marketing"[^>]*\schecked', html)
    assert re.search(r'id="prefs-necessary"[^>]*\schecked[^>]*\sdisabled', html)
    assert "no-store" in response["Cache-Control"]
    assert 'content="noindex, nofollow"' in html


def test_the_settings_page_works_in_italian(client: Client) -> None:
    html = client.get("/it/cookie-settings/").content.decode()
    assert "Impostazioni dei cookie" in html


# --- The banner is the same for everyone (page cache safe)


def banner_of(response_content: bytes) -> str:
    html = response_content.decode()
    match = re.search(r'<section class="consent-banner".*?</section>', html, re.S)
    assert match, "no consent banner on the page"
    return match[0]


def test_the_banner_markup_does_not_depend_on_the_visitors_choice(client: Client) -> None:
    anonymous = banner_of(client.get("/").content)
    other = Client()
    other.post("/consent/", {"action": "accept_all"}, headers=SAME_SITE)
    decided = banner_of(other.get("/").content)
    assert anonymous == decided
    assert " hidden" in anonymous.split(">", 1)[0]


def test_the_banner_offers_reject_and_accept_with_equal_weight(client: Client) -> None:
    banner = banner_of(client.get("/").content)
    buttons = dict(
        re.findall(
            r'<button type="submit" class="([^"]*)" name="action" value="(reject_all|accept_all)"',
            banner,
        )
    )
    by_value = {
        value: classes
        for classes, value in re.findall(r'class="([^"]*)" name="action" value="(\w+)"', banner)
    }
    assert by_value["reject_all"] == by_value["accept_all"]
    assert buttons or by_value


def test_the_footer_links_to_the_settings_page(client: Client) -> None:
    html = client.get("/").content.decode()
    assert re.search(
        r'<a class="consent-settings-link" href="/cookie-settings/">Cookie settings</a>', html
    )


def test_the_banner_markup_has_no_inline_script_or_handler(client: Client) -> None:
    banner = banner_of(client.get("/").content)
    assert "<script" not in banner
    assert not re.search(r"\son\w+=", banner)
    assert 'style="' not in banner


# --- The provider setting (SEO.9)


def test_the_default_provider_is_ours() -> None:
    assert django_settings.MPMV_CONSENT_PROVIDER == "self-hosted"
    assert isinstance(get_provider(), SelfHostedConsentProvider)


def test_an_unknown_provider_fails_the_system_check() -> None:
    with override_settings(MPMV_CONSENT_PROVIDER="nope"):
        (error,) = check_consent_provider(None)
    assert isinstance(error, Error)
    assert error.id == "seo.E001"
    assert "nope" in error.msg


def test_a_provider_that_cannot_be_imported_fails_the_system_check() -> None:
    with override_settings(
        MPMV_CONSENT_PROVIDER="broken", MPMV_CONSENT_PROVIDERS={"broken": "no.such.Module"}
    ):
        (error,) = check_consent_provider(None)
    assert error.id == "seo.E001"


def test_a_class_that_is_not_a_provider_fails_the_system_check() -> None:
    with override_settings(
        MPMV_CONSENT_PROVIDER="odd", MPMV_CONSENT_PROVIDERS={"odd": "collections.OrderedDict"}
    ):
        (error,) = check_consent_provider(None)
    assert error.id == "seo.E001"


def test_the_default_configuration_passes_the_check() -> None:
    assert check_consent_provider(None) == []


def test_a_missing_csp_origin_is_a_warning() -> None:
    with override_settings(**FAKE):
        (warning,) = check_consent_provider(None)
    assert isinstance(warning, Warning)
    assert warning.id == "seo.W001"
    csp = {
        **django_settings.SECURE_CSP,
        "script-src": [*django_settings.SECURE_CSP["script-src"], "https://cmp.example.test"],  # type: ignore[misc]
    }
    with override_settings(SECURE_CSP=csp, **FAKE):
        assert check_consent_provider(None) == []


def test_a_page_still_renders_when_the_provider_is_misconfigured(client: Client) -> None:
    with override_settings(MPMV_CONSENT_PROVIDER="nope"):
        response = client.get("/")
    assert response.status_code == 200
    assert b"consent-banner" not in response.content


# --- Tag gating: the same code with either provider


def render(source: str, **context: object) -> str:
    return Template("{% load i18n %}" + source).render(Context(context))


def assert_inert(markup: str) -> None:
    """A gated script never has a loadable `src`: no tag can fire before consent."""
    scripts = re.findall(r"<script\b[^>]*>", markup)
    assert scripts, markup
    for script in scripts:
        assert 'type="text/plain"' in script, script
        assert not re.search(r"\ssrc=", script), script


def test_a_gated_script_is_inert_with_the_default_provider() -> None:
    markup = render('{% consent_script "statistics" "https://stats.example.test/tag.js" %}')
    assert_inert(markup)
    assert 'data-consent="statistics"' in markup
    assert 'data-src="https://stats.example.test/tag.js"' in markup


def test_a_gated_script_is_inert_with_another_provider() -> None:
    with override_settings(**FAKE):
        markup = render('{% consent_script "statistics" "https://stats.example.test/tag.js" %}')
    assert_inert(markup)
    assert "data-cmp-category" in markup
    assert "data-consent=" not in markup  # the tag code asked the provider, it wrote nothing itself


def test_a_test_provider_replaces_the_banner_without_touching_the_tags(client: Client) -> None:
    with override_settings(**FAKE):
        html = client.get("/").content.decode()
        assert 'id="fake-cmp-banner"' in html
        assert 'class="consent-banner"' not in html
        assert (
            client.post("/consent/", {"action": "accept_all"}, headers=SAME_SITE).status_code == 404
        )
        assert client.get("/cookie-settings/").status_code == 404
        assert 'class="consent-settings-link"' not in html


def test_the_provider_answers_for_the_state_and_the_signals() -> None:
    request = RequestFactory().get("/")
    with override_settings(**FAKE):
        provider = get_provider()
        assert not provider.allows(request, engine.STATISTICS)
        assert provider.allows(request, engine.NECESSARY)
        assert provider.signals(request)["analytics_storage"] == engine.DENIED
        FakeConsentProvider.granted = {engine.STATISTICS}
        assert provider.allows(request, engine.STATISTICS)
        assert provider.signals(request)["analytics_storage"] == engine.GRANTED


def test_the_self_hosted_provider_reads_the_cookie_and_denies_before_a_choice() -> None:
    provider = get_provider()
    plain = RequestFactory().get("/")
    assert not provider.allows(plain, engine.MARKETING)
    value = engine.encode_cookie(
        engine.ConsentState(True, frozenset({"marketing"}), engine.BANNER_VERSION, "b" * 32)
    )
    decided = RequestFactory().get("/")
    decided.COOKIES[engine.COOKIE_NAME] = value
    assert provider.allows(decided, engine.MARKETING)
    assert not provider.allows(decided, engine.STATISTICS)


@pytest.mark.parametrize(
    ("category", "src"),
    [
        ("necessary", "https://x.example/a.js"),
        ("bogus", "https://x.example/a.js"),
        ("statistics", "http://x.example/a.js"),
        ("statistics", "//x.example/a.js"),
        ("statistics", "javascript:alert(1)"),
        ("statistics", 'https://x.example/a.js"><script>'),
        ("statistics", ""),
    ],
)
def test_a_gated_script_refuses_an_unknown_category_or_an_unsafe_source(
    category: str, src: str
) -> None:
    with pytest.raises(ValueError, match=r"gated script|not a category"):
        get_provider().gate_script(category, src)


def test_a_same_site_path_is_a_valid_source() -> None:
    assert 'data-src="/static/vendor/tag.js"' in get_provider().gate_script(
        "marketing", "/static/vendor/tag.js"
    )
