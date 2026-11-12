"""ARC.32 (part 1): the help entries, their lookup and the endpoint that serves them.

Done when (this part): a translatable `HelpEntry` snippet keyed by route name or page type, rich
text through the CMS.12 sanitiser, a general entry as the fallback (seeded, and empty-safe when
staff delete it); the resolver answers route name, then page type, then the general entry, per
language and cached; the endpoint is GET only, for signed-in users only and never cached.
"""

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.models import Q
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail.models import Locale

from apps.accounts.tests.factories import UserFactory
from apps.cms import help as help_lookup
from apps.cms.models import HelpEntry
from apps.cms.tests.factories import HelpEntryFactory
from tests import hostile_input as hi
from tests.unit.field_help_allowlist import ALLOWED_FIELD_NAMES

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    cache.clear()


def general(language: str = "en") -> HelpEntry:
    return HelpEntry.objects.get(kind="general", locale__language_code=language)


def locale(code: str) -> Locale:
    return Locale.objects.get(language_code=code)


# --- The model


def test_a_general_help_entry_is_seeded_for_the_primary_language() -> None:
    entry = general()
    assert entry.key == ""
    assert entry.title
    assert entry.body


def test_an_entry_is_one_per_language_kind_and_key() -> None:
    HelpEntryFactory.create(kind="route", key="home")
    with pytest.raises(IntegrityError):
        HelpEntryFactory.create(kind="route", key="home")


def test_the_same_key_may_exist_in_another_language() -> None:
    HelpEntryFactory.create(kind="route", key="home")
    HelpEntryFactory.create(kind="route", key="home", locale=locale("it"))


@pytest.mark.parametrize(
    ("kind", "key"),
    [("general", "home"), ("route", ""), ("page_type", ""), ("route", "has space")],
)
def test_the_key_must_fit_the_kind(kind: str, key: str) -> None:
    entry = HelpEntry(kind=kind, key=key, title="T", body="<p>b</p>", locale=locale("en"))
    with pytest.raises(ValidationError):
        entry.full_clean()


def test_help_text_is_stored_through_the_sanitiser() -> None:
    entry = HelpEntryFactory.create(kind="route", key="home", body=hi.HTML[1] + "<p>kept</p>")
    stored = HelpEntry.objects.get(pk=entry.pk).body
    assert hi.active_markup(stored) == []
    assert "kept" in stored


def test_every_field_has_a_help_text() -> None:
    # id, translation_key and locale are Wagtail's own and hidden from the form; names the ARC.30
    # sweep allow-lists (such as "title") are self-explanatory and carry no help text on purpose.
    own = {"id", "translation_key", "locale", *ALLOWED_FIELD_NAMES}
    fields = [f for f in HelpEntry._meta.fields if f.name not in own]
    assert [f.name for f in fields if not f.help_text] == []


# --- The resolver


def test_the_route_entry_answers_before_the_page_type_and_the_general_entry() -> None:
    HelpEntryFactory.create(kind="route", key="home", title="Route help")
    HelpEntryFactory.create(kind="page_type", key="cms.StandardPage", title="Type help")
    found = help_lookup.help_for("home", "cms.StandardPage", "en")
    assert found is not None
    assert (found.title, found.source) == ("Route help", "route")


def test_the_page_type_entry_answers_when_the_route_has_none() -> None:
    HelpEntryFactory.create(kind="page_type", key="cms.StandardPage", title="Type help")
    found = help_lookup.help_for("home", "cms.StandardPage", "en")
    assert found is not None
    assert (found.title, found.source) == ("Type help", "page_type")


def test_a_screen_without_an_entry_gets_the_general_one() -> None:
    found = help_lookup.help_for("no:such:route", "", "en")
    assert found is not None
    assert found.source == "general"
    assert found.title == general().title


def test_the_answer_follows_the_language_and_falls_back_to_the_primary_one() -> None:
    HelpEntryFactory.create(kind="route", key="home", title="Help")
    HelpEntryFactory.create(kind="route", key="home", title="Aiuto", locale=locale("it"))
    assert help_lookup.help_for("home", "", "it").title == "Aiuto"  # type: ignore[union-attr]
    assert help_lookup.help_for("home", "", "de").title == "Help"  # type: ignore[union-attr]


def test_without_any_entry_the_lookup_is_empty_not_an_error() -> None:
    HelpEntry.objects.all().delete()
    assert help_lookup.help_for("home", "cms.StandardPage", "en") is None


def test_the_answer_is_cached_per_language_and_dropped_when_an_entry_changes() -> None:
    entry = HelpEntryFactory.create(kind="route", key="home", title="First")
    help_lookup.help_for("home", "", "en")
    with CaptureQueriesContext(connection) as queries:
        again = help_lookup.help_for("home", "", "en")
    assert again is not None
    assert again.title == "First"
    assert len(queries) == 0
    entry.title = "Second"
    entry.save()
    changed = help_lookup.help_for("home", "", "en")
    assert changed is not None
    assert changed.title == "Second"
    entry.delete()
    after = help_lookup.help_for("home", "", "en")
    assert after is not None
    assert after.source == "general"


def test_the_body_is_rendered_from_the_stored_rich_text() -> None:
    HelpEntryFactory.create(kind="route", key="home", body="<p>Hello <b>there</b></p>")
    found = help_lookup.help_for("home", "", "en")
    assert found is not None
    assert "<b>there</b>" in found.html


# --- The endpoint

URL = "/_help/entry/"


def signed_in() -> Client:
    client = Client()
    client.force_login(UserFactory.create())
    return client


def test_the_endpoint_has_a_name() -> None:
    assert reverse("help_entry") == URL


def test_an_anonymous_visitor_gets_403_and_no_text() -> None:
    HelpEntryFactory.create(kind="route", key="home", title="Private words")
    response = Client().get(URL, {"route": "home"})
    assert response.status_code == 403
    assert b"Private words" not in response.content
    assert "no-store" in response["Cache-Control"]


def test_a_signed_in_user_gets_the_entry_as_a_fragment() -> None:
    HelpEntryFactory.create(kind="route", key="home", title="About this page")
    response = signed_in().get(URL, {"route": "home"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert "About this page" in body
    assert "<html" not in body  # a fragment for the dialog
    assert 'data-help-source="route"' in body


def test_the_answer_is_never_cached() -> None:
    response = signed_in().get(URL, {"route": "home"})
    cache_control = response["Cache-Control"]
    assert "no-store" in cache_control
    assert "private" in cache_control


def test_only_get_is_allowed() -> None:
    client = signed_in()
    assert client.post(URL, {"route": "home"}).status_code == 405
    assert client.delete(URL).status_code == 405


def test_an_unknown_route_gets_the_general_entry() -> None:
    response = signed_in().get(URL, {"route": "no:such:route"})
    assert response.status_code == 200
    assert 'data-help-source="general"' in response.content.decode()
    assert general().title in response.content.decode()


def test_a_hostile_parameter_is_ignored() -> None:
    response = signed_in().get(URL, {"route": "<script>alert(1)</script>", "page_type": "a b"})
    assert response.status_code == 200
    assert "<script>alert" not in response.content.decode()
    assert 'data-help-source="general"' in response.content.decode()


def test_the_language_parameter_selects_the_entry_and_a_bad_one_is_ignored() -> None:
    HelpEntryFactory.create(kind="route", key="home", title="Help")
    HelpEntryFactory.create(kind="route", key="home", title="Aiuto", locale=locale("it"))
    client = signed_in()
    assert "Aiuto" in client.get(URL, {"route": "home", "lang": "it"}).content.decode()
    assert "Help" in client.get(URL, {"route": "home", "lang": "zz"}).content.decode()


def test_an_empty_catalog_answers_a_polite_message() -> None:
    HelpEntry.objects.all().delete()
    response = signed_in().get(URL, {"route": "home"})
    assert response.status_code == 200
    assert "help-entry__empty" in response.content.decode()


def test_the_vendor_guide_is_linked_only_when_asked_and_configured(settings: object) -> None:
    HelpEntryFactory.create(kind="route", key="home", show_vendor_guide=True)
    client = signed_in()
    settings.MPMV_VENDOR_GUIDE_URL = ""  # type: ignore[attr-defined]
    cache.clear()
    assert "help-entry__guide" not in client.get(URL, {"route": "home"}).content.decode()
    settings.MPMV_VENDOR_GUIDE_URL = "https://guide.example.org/vendors/"  # type: ignore[attr-defined]
    cache.clear()
    body = client.get(URL, {"route": "home"}).content.decode()
    assert 'href="https://guide.example.org/vendors/"' in body
    HelpEntry.objects.filter(~Q(kind="general")).update(show_vendor_guide=False)
    cache.clear()
    assert "help-entry__guide" not in client.get(URL, {"route": "home"}).content.decode()


# --- Security review of #279


def test_a_row_written_around_the_save_hook_is_sanitised_when_rendered() -> None:
    entry = HelpEntryFactory.create(kind="route", key="home", body="<p>clean</p>")
    HelpEntry.objects.filter(pk=entry.pk).update(
        body='<p>kept</p><script>alert(1)</script><img src=x onerror="alert(2)">'
    )
    cache.clear()
    found = help_lookup.help_for("home", "", "en")
    assert found is not None
    assert "kept" in found.html
    assert hi.active_markup(found.html) == []
    body = signed_in().get(URL, {"route": "home"}).content.decode()
    assert "<script>alert" not in body
    assert "onerror" not in body


def test_two_long_keys_make_cache_keys_within_the_backend_limit() -> None:
    first, second = "a" * 120, "b" * 119 + "c"
    HelpEntryFactory.create(kind="route", key=first, title="First")
    HelpEntryFactory.create(kind="page_type", key=second, title="Second")
    found = help_lookup.help_for(first, second, "en")
    assert found is not None
    assert found.title == "First"
    keys = [str(key) for key in cache._cache]  # type: ignore[attr-defined]
    assert any(key.startswith(":1:cms:help:") for key in keys)
    assert all(len(key) < 250 for key in keys)


def test_an_unknown_key_gets_the_general_entry_and_caches_no_new_key() -> None:
    HelpEntryFactory.create(kind="route", key="home", title="Known")
    help_lookup.help_for("home", "", "en")
    before = set(cache._cache)  # type: ignore[attr-defined]
    for n in range(20):
        found = help_lookup.help_for(f"made:up:{n}", f"cms.Made{n}", "en")
        assert found is not None
        assert found.source == "general"
    after = set(cache._cache)  # type: ignore[attr-defined]
    assert len(after - before) <= 1  # the one shared answer of "no known key", not one per key


def test_the_endpoint_ignores_unknown_routes_without_growing_the_cache() -> None:
    client = signed_in()
    client.get(URL, {"route": "known-none-0"})
    before = set(cache._cache)  # type: ignore[attr-defined]
    for n in range(10):
        response = client.get(URL, {"route": f"made:up:{n}", "page_type": f"x.Made{n}"})
        assert 'data-help-source="general"' in response.content.decode()
    assert set(cache._cache) == before  # type: ignore[attr-defined]
