"""EVT.8: multilingual events (owner decision Q-72, D-18 exception; spec 15 "Languages",
regression cases 9, 10 (HTML part) and 11).

An event is written in any enabled language; translations are optional and made with
`add_event_translation` (Wagtail `copy_for_translation`); the structured fields follow the
original on every publish; the listings fall back to the original language with a `lang`
attribute, the `hreflang` of the link and the language name; `hreflang` and the sitemaps list
existing translations only.
"""

import datetime as dt
import re
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone
from wagtail.models import Locale, Site

from apps.accounts.models import User
from apps.cms.services import seed_site
from apps.events import services
from apps.events.models import EventIndexPage, EventPage, PolicyMode, VendorEventPolicy
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _locale(code: str) -> Locale:
    return Locale.objects.get(language_code=code)


def _fresh(page: EventPage) -> EventPage:
    found: EventPage = EventPage.objects.get(pk=page.pk)
    return found


def _data(**changes: Any) -> dict[str, Any]:
    start = (timezone.now() + dt.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    data: dict[str, Any] = {
        "title": "Herbstmesse",
        "summary": "Treffen Sie die Hersteller auf der Herbstmesse.",
        "event_type": "fair",
        "starts_at": start,
        "ends_at": start + dt.timedelta(hours=8),
        "timezone": "Europe/Rome",
        "attendance": "in_person",
        "venue_name": "Messe Halle 3",
        "city": "Bolzano",
        "country": "IT",
    }
    data.update(changes)
    return data


class Fixture:
    def __init__(self, *, trusted: bool = True) -> None:
        self.vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
        self.user: User = VendorMemberFactory.create(vendor=self.vendor).user
        if trusted:
            VendorEventPolicy.objects.create(vendor=self.vendor, mode=PolicyMode.TRUSTED)

    def create(self, language: str = "de", **changes: Any) -> EventPage:
        page = services.save_event_draft(
            self.user, _data(**changes), vendor=self.vendor, locale=_locale(language)
        ).page
        assert page is not None
        services.submit_event(self.user, page)
        return _fresh(page)

    def translate(self, page: EventPage, language: str, **text: Any) -> EventPage:
        data = {"title": "Fiera d'autunno", "summary": "Incontra gli artigiani."} | text
        translated = services.add_event_translation(self.user, page, _locale(language), data).page
        assert translated is not None
        services.submit_event(self.user, translated)
        return _fresh(translated)


def _get(client: Client, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status, (url, response.status_code)
    return response.content.decode()


# --- Case 9: any original language


def test_an_event_written_in_german_has_german_pages_only(client: Client) -> None:
    page = Fixture().create("de")
    assert page.original_locale.language_code == "de"
    assert page.locale.language_code == "de"
    assert page.url == f"/de/events/{page.slug}/"
    _get(client, page.url)
    _get(client, f"/it/events/{page.slug}/", 404)
    _get(client, f"/events/{page.slug}/", 404)
    assert (
        not EventPage.objects.filter(translation_key=page.translation_key)
        .exclude(pk=page.pk)
        .exists()
    )


def test_hreflang_of_an_original_only_event_lists_it_with_x_default(client: Client) -> None:
    page = Fixture().create("de")
    html = _get(client, page.url)
    alternates = re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html)
    assert [code for code, _url in alternates] == ["de", "x-default"]
    assert all(url.endswith(f"/de/events/{page.slug}/") for _code, url in alternates)


def test_the_sitemaps_list_the_event_in_its_own_language_only(client: Client) -> None:
    page = Fixture().create("de")
    for code in ("de", "en", "it", "fr", "es"):
        path = f"/sitemap-{code}-events.xml"
        body = client.get(path).content.decode()
        assert (f"/de/events/{page.slug}/" in body) is (code == "de"), path


def test_a_language_that_is_not_enabled_is_refused() -> None:
    fixture = Fixture()
    other = Locale.objects.create(language_code="pt")
    with pytest.raises(ValidationError):
        services.save_event_draft(fixture.user, _data(), vendor=fixture.vendor, locale=other)


# --- Add translation


def test_add_translation_makes_a_draft_page_with_the_same_translation_key() -> None:
    fixture = Fixture()
    original = fixture.create("de")
    changes = services.add_event_translation(
        fixture.user, original, _locale("it"), {"title": "Fiera", "summary": "Incontra."}
    )
    translated = changes.page
    assert translated is not None
    assert translated.translation_key == original.translation_key
    assert translated.locale.language_code == "it"
    assert translated.original_locale.language_code == "de"
    assert translated.live is False
    assert translated.status == EventPage.Status.DRAFT
    assert translated.get_parent().specific == EventIndexPage.objects.get(
        locale__language_code="it"
    )
    latest = translated.get_latest_revision_as_object()
    assert (latest.title, latest.summary) == ("Fiera", "Incontra.")
    assert latest.starts_at == original.starts_at


def test_add_translation_dry_run_writes_nothing() -> None:
    fixture = Fixture()
    original = fixture.create("de")
    changes = services.add_event_translation(
        fixture.user, original, _locale("it"), {"title": "Fiera", "summary": "x"}, dry_run=True
    )
    assert changes.page is None
    assert not EventPage.objects.filter(locale=_locale("it")).exists()


def test_add_translation_refuses_the_same_language_twice_and_the_original_language() -> None:
    fixture = Fixture()
    original = fixture.create("de")
    for language in ("de",):
        with pytest.raises(ValidationError):
            services.add_event_translation(
                fixture.user, original, _locale(language), {"title": "T", "summary": "S"}
            )
    fixture.translate(original, "it")
    with pytest.raises(ValidationError):
        services.add_event_translation(
            fixture.user, original, _locale("it"), {"title": "T", "summary": "S"}
        )


def test_add_translation_takes_text_fields_only() -> None:
    fixture = Fixture()
    original = fixture.create("de")
    with pytest.raises(ValidationError) as error:
        services.add_event_translation(
            fixture.user, original, _locale("it"), {"title": "T", "summary": "S", "city": "Roma"}
        )
    assert "city" in error.value.message_dict


def test_another_vendors_member_cannot_translate_the_event() -> None:
    original = Fixture().create("de")
    stranger = Fixture()
    from django.http import Http404

    with pytest.raises(Http404):
        services.add_event_translation(
            stranger.user, original, _locale("it"), {"title": "T", "summary": "S"}
        )


def test_a_translation_of_a_hidden_event_is_refused_to_its_vendor() -> None:
    fixture = Fixture()
    original = fixture.create("de")
    from apps.accounts.tests.factories import UserFactory

    staff = UserFactory.create(is_staff=True, is_superuser=True)
    services.hide_event(staff, original, "spam")
    with pytest.raises(services.EventRefused):
        services.add_event_translation(
            fixture.user, original, _locale("it"), {"title": "T", "summary": "S"}
        )


# --- Case 10 (HTML): the original-language fallback


def _article(html: str, title: str) -> str:
    for block in re.findall(r"<article[^>]*event-summary[^>]*>.*?</article>", html, re.S):
        if title in block:
            return str(block)
    raise AssertionError(f"no article for {title!r}")


def test_the_italian_list_shows_the_german_event_with_lang_hreflang_and_the_language_name(
    client: Client,
) -> None:
    page = Fixture().create("de", title="Herbstmesse")
    html = _get(client, "/it/events/")
    article = _article(html, "Herbstmesse")
    assert re.search(r"<article[^>]*\blang=\"de\"", article)
    link = re.search(r"<a [^>]*>", article)
    assert link
    assert 'hreflang="de"' in link.group(0)
    assert f'href="/de/events/{page.slug}/"' in link.group(0)
    assert "Deutsch" in article
    assert "1 event" in html


def test_after_an_italian_translation_the_list_shows_it_without_a_lang_override(
    client: Client,
) -> None:
    fixture = Fixture()
    page = fixture.create("de", title="Herbstmesse")
    italian = fixture.translate(page, "it", title="Fiera d'autunno")
    html = _get(client, "/it/events/")
    article = _article(html, "Fiera d&#x27;autunno")
    assert not re.search(r"<article[^>]*\blang=", article)
    assert f'href="{italian.url}"' in article
    assert "Herbstmesse" not in html
    assert "event-language" not in html
    # Each event once: no duplicate row for the original.
    assert html.count("event-summary") == 1


def test_the_german_list_shows_the_german_text_with_no_lang_override(client: Client) -> None:
    Fixture().create("de", title="Herbstmesse")
    html = _get(client, "/de/events/")
    article = _article(html, "Herbstmesse")
    assert not re.search(r"<article[^>]*\blang=", article)


def test_the_english_list_falls_back_to_the_original_not_to_another_translation(
    client: Client,
) -> None:
    fixture = Fixture()
    page = fixture.create("de", title="Herbstmesse")
    fixture.translate(page, "it", title="Fiera d'autunno")
    html = _get(client, "/events/")
    article = _article(html, "Herbstmesse")
    assert re.search(r"<article[^>]*\blang=\"de\"", article)
    assert "Fiera" not in html


def test_the_fallback_marks_stay_on_the_month_view(client: Client) -> None:
    Fixture().create("de", title="Herbstmesse")
    start = timezone.now() + dt.timedelta(days=10)
    html = _get(client, f"/it/events/calendar/{start:%Y-%m}/")
    assert 'lang="de"' in _article(html, "Herbstmesse")
    assert "Deutsch" in _article(html, "Herbstmesse")


def test_only_events_in_filter_limits_the_list_to_real_translations(client: Client) -> None:
    fixture = Fixture()
    german = fixture.create("de", title="Herbstmesse")
    both = fixture.create("de", title="Frühlingsmesse")
    fixture.translate(both, "it", title="Fiera di primavera")
    html = _get(client, "/it/events/?translated=1")
    assert "Fiera di primavera" in html
    assert "Herbstmesse" not in html
    assert "1 event" in html
    assert 'name="translated"' in _get(client, "/it/events/")
    assert german.slug  # the fallback event stays in the unfiltered list
    assert "Herbstmesse" in _get(client, "/it/events/")


def test_the_only_events_filter_is_noindex_and_keeps_the_choice_in_the_form(
    client: Client,
) -> None:
    Fixture().create("de")
    html = _get(client, "/it/events/?translated=1")
    assert re.search(r'<meta name="robots" content="noindex', html)
    assert re.search(r'<input[^>]*name="translated"[^>]*checked', html)


def test_the_fallback_follows_the_original_even_when_the_translation_is_a_draft(
    client: Client,
) -> None:
    fixture = Fixture()
    page = fixture.create("de", title="Herbstmesse")
    services.add_event_translation(
        fixture.user, page, _locale("it"), {"title": "Bozza", "summary": "x"}
    )
    html = _get(client, "/it/events/")
    assert "Bozza" not in html
    assert 'lang="de"' in _article(html, "Herbstmesse")


def test_events_for_locale_returns_each_event_once_in_the_best_language() -> None:
    fixture = Fixture()
    german = fixture.create("de", title="Herbstmesse")
    both = fixture.create("de", title="Frühlingsmesse")
    italian = fixture.translate(both, "it", title="Fiera di primavera")
    rows = services.events_for_locale(_locale("it"), None)
    by_title = {row.page.title: row for row in rows}
    assert set(by_title) == {"Herbstmesse", "Fiera di primavera"}
    assert by_title["Herbstmesse"].fallback is True
    assert by_title["Herbstmesse"].language == "de"
    assert by_title["Herbstmesse"].page.pk == german.pk
    assert by_title["Fiera di primavera"].fallback is False
    assert by_title["Fiera di primavera"].page.pk == italian.pk


def test_a_hidden_event_leaves_every_language_list(client: Client) -> None:
    from apps.accounts.tests.factories import UserFactory

    fixture = Fixture()
    page = fixture.create("de", title="Herbstmesse")
    services.hide_event(UserFactory.create(is_staff=True, is_superuser=True), page, "spam")
    assert "Herbstmesse" not in _get(client, "/it/events/")


# --- hreflang with translations


def test_hreflang_lists_existing_translations_with_x_default_on_the_english_one(
    client: Client,
) -> None:
    fixture = Fixture()
    page = fixture.create("de")
    fixture.translate(page, "it")
    html = _get(client, page.url)
    alternates = re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html)
    assert [code for code, _url in alternates] == ["it", "de", "x-default"]
    default = dict(alternates)["x-default"]
    assert default.endswith(f"/de/events/{page.slug}/")
    fixture.translate(page, "en", title="Autumn fair")
    html = _get(client, page.url)
    by_code = dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html))
    assert set(by_code) == {"de", "it", "en", "x-default"}
    assert by_code["x-default"] == by_code["en"]


def test_a_draft_translation_is_not_in_hreflang_and_has_no_url_to_visitors(
    client: Client,
) -> None:
    fixture = Fixture()
    page = fixture.create("de")
    draft = services.add_event_translation(
        fixture.user, page, _locale("fr"), {"title": "Salon", "summary": "x"}
    ).page
    assert draft is not None
    html = _get(client, page.url)
    assert 'hreflang="fr"' not in html
    _get(client, f"/fr/events/{draft.slug}/", 404)
    assert f"/fr/events/{draft.slug}/" not in client.get("/sitemap-fr-events.xml").content.decode()


def test_no_fallback_copy_of_an_event_page_exists_in_a_language_without_a_translation(
    client: Client,
) -> None:
    fixture = Fixture()
    page = fixture.create("de")
    fixture.translate(page, "it")
    for code in ("en", "fr", "es"):
        prefix = "" if code == "en" else f"/{code}"
        _get(client, f"{prefix}/events/{page.slug}/", 404)
    assert EventPage.objects.filter(translation_key=page.translation_key).count() == 2


def test_the_sitemap_of_a_language_lists_its_translation_only(client: Client) -> None:
    fixture = Fixture()
    page = fixture.create("de")
    italian = fixture.translate(page, "it")
    it_map = client.get("/sitemap-it-events.xml").content.decode()
    de_map = client.get("/sitemap-de-events.xml").content.decode()
    assert f"/it/events/{italian.slug}/" in it_map
    assert f"/de/events/{page.slug}/" not in it_map
    assert f"/de/events/{page.slug}/" in de_map
    assert f"/it/events/{italian.slug}/" not in de_map


# --- Case 11: synchronised structured fields and moderation


def test_changing_the_dates_on_the_original_changes_every_translation() -> None:
    fixture = Fixture()
    page = fixture.create("de")
    italian = fixture.translate(page, "it")
    french = fixture.translate(page, "fr")
    assert page.starts_at is not None
    new_start = page.starts_at + dt.timedelta(days=3)
    services.save_event_draft(
        fixture.user,
        {"starts_at": new_start, "ends_at": new_start + dt.timedelta(hours=2), "city": "Merano"},
        page,
    )
    services.submit_event(fixture.user, page)
    for translation in (italian, french):
        fresh = _fresh(translation)
        assert fresh.starts_at == new_start
        assert fresh.ends_at == new_start + dt.timedelta(hours=2)
        assert fresh.city == "Merano"
        assert fresh.sequence == _fresh(page).sequence


def test_a_translations_own_edit_never_diverges_the_structured_fields() -> None:
    fixture = Fixture()
    page = fixture.create("de")
    italian = fixture.translate(page, "it")
    assert page.starts_at is not None
    later = page.starts_at + dt.timedelta(days=9)
    services.save_event_draft(
        fixture.user,
        {
            "title": "Fiera nuova",
            "city": "Roma",
            "starts_at": later,
            "ends_at": later + dt.timedelta(hours=1),
        },
        italian,
    )
    services.submit_event(fixture.user, italian)
    fresh = _fresh(italian)
    assert fresh.title == "Fiera nuova"
    assert fresh.city == page.city
    assert fresh.starts_at == page.starts_at


def test_products_and_categories_are_copied_to_the_translations() -> None:
    from apps.catalog.tests.factories import CategoryFactory, ProductFactory

    fixture = Fixture()
    page = fixture.create("de")
    italian = fixture.translate(page, "it")
    product = ProductFactory.create(vendor=fixture.vendor)
    category = CategoryFactory.create()
    services.save_event_draft(
        fixture.user, {"products": [product.pk], "categories": [category.pk]}, page
    )
    services.submit_event(fixture.user, page)
    fresh = _fresh(italian)
    assert list(fresh.products.all()) == [product]
    assert list(fresh.categories.all()) == [category]


def test_cancelling_the_event_cancels_every_translation() -> None:
    fixture = Fixture()
    page = fixture.create("de")
    italian = fixture.translate(page, "it")
    services.cancel_event(fixture.user, page)
    assert _fresh(italian).cancelled_at is not None
    assert _fresh(page).cancelled_at is not None


def test_a_first_translation_goes_through_review_and_is_not_a_new_event() -> None:
    fixture = Fixture(trusted=False)
    # The vendor's first events are reviewed: threshold 3 by default.
    page = services.save_event_draft(
        fixture.user, _data(), vendor=fixture.vendor, locale=_locale("de")
    ).page
    assert page is not None
    services.submit_event(fixture.user, page)
    assert _fresh(page).status == EventPage.Status.PENDING
    _approve(page)
    assert _fresh(page).live
    from apps.events.models import approved_event_count

    assert approved_event_count(fixture.vendor.pk) == 1
    translated = services.add_event_translation(
        fixture.user, page, _locale("it"), {"title": "Fiera", "summary": "x"}
    ).page
    assert translated is not None
    services.submit_event(fixture.user, translated)
    assert _fresh(translated).status == EventPage.Status.PENDING
    assert not _fresh(translated).live
    _approve(translated)
    assert _fresh(translated).live
    assert approved_event_count(fixture.vendor.pk) == 1  # one event, two pages


def _approve(page: EventPage) -> None:
    from apps.accounts.tests.factories import UserFactory

    state = _fresh(page).current_workflow_state
    assert state is not None
    task = state.current_task_state
    assert task is not None
    moderator = UserFactory.create(is_staff=True, is_superuser=True)
    task.approve(user=moderator)
