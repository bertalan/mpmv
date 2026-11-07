"""CMS.1: Wagtail site, locales and page tree. Done when: EN/IT home and static pages.

One page tree per language under the Wagtail root: the EN home is the default site's root, and
the IT and DE homes are its translations (same `translation_key`), each with its static pages.
"""

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.sql import emit_post_migrate_signal
from django.test import override_settings
from wagtail.models import Locale, Page, Site

from apps.cms.models import HomePage, NavigationMenu, StandardPage
from apps.cms.services import SeedReport, seed_site, sync_locales
from apps.cms.tests.factories import HomePageFactory, StandardPageFactory

pytestmark = pytest.mark.django_db

LANGUAGES_PLUS_FRENCH = [*settings.LANGUAGES, ("fr", "Français")]


def _codes() -> list[str]:
    return [code for code, _name in settings.LANGUAGES]


# --- Locales come from settings.LANGUAGES


def test_every_configured_language_has_a_wagtail_locale() -> None:
    assert set(Locale.objects.values_list("language_code", flat=True)) >= set(_codes())
    assert Locale.get_default().language_code == settings.LANGUAGE_CODE == "en"


@override_settings(LANGUAGES=LANGUAGES_PLUS_FRENCH)
def test_a_language_added_in_settings_gets_its_locale_without_a_migration() -> None:
    assert sync_locales() == ["fr"]
    assert Locale.objects.filter(language_code="fr").exists()
    assert sync_locales() == []  # idempotent


def test_migrate_syncs_the_locales() -> None:
    Locale.objects.filter(language_code="de").delete()
    emit_post_migrate_signal(verbosity=0, interactive=False, db="default")  # end of `migrate`
    assert Locale.objects.filter(language_code="de").exists()


# --- Site and page tree


def test_after_migrate_the_default_site_serves_an_english_home_page() -> None:
    site = Site.objects.get(is_default_site=True)
    home = site.root_page.specific
    assert isinstance(home, HomePage)
    assert home.locale.language_code == "en"
    assert home.live
    assert home.depth == 2  # directly under the tree root


def test_seed_creates_a_home_and_a_static_page_per_language(seeded: SeedReport) -> None:
    en_home = Site.objects.get(is_default_site=True).root_page.specific
    homes = {page.locale.language_code: page for page in HomePage.objects.live()}
    assert set(homes) == set(_codes())
    assert {page.translation_key for page in homes.values()} == {en_home.translation_key}
    assert all(page.depth == 2 for page in homes.values())

    abouts = {page.locale.language_code: page for page in StandardPage.objects.live()}
    assert set(abouts) == set(_codes())
    assert len({page.translation_key for page in abouts.values()}) == 1
    for code, about in abouts.items():
        assert about.get_parent().specific == homes[code]
    assert (abouts["en"].slug, abouts["it"].slug, abouts["de"].slug) == (
        "about",
        "chi-siamo",
        "ueber-uns",
    )


def test_seed_creates_a_header_and_a_footer_menu_per_language(seeded: SeedReport) -> None:
    for code in _codes():
        locations = set(
            NavigationMenu.objects.filter(locale__language_code=code).values_list(
                "location", flat=True
            )
        )
        assert locations == {"header", "footer"}


def test_seed_is_idempotent(seeded: SeedReport) -> None:
    assert seeded.created
    pages = Page.objects.count()
    assert seed_site().created == []
    assert Page.objects.count() == pages


def test_seed_rebuilds_the_site_on_an_empty_tree() -> None:
    Site.objects.all().delete()
    Page.objects.filter(depth=1).delete()  # the whole tree
    report = seed_site()
    site = Site.objects.get(is_default_site=True)
    assert isinstance(site.root_page.specific, HomePage)
    assert HomePage.objects.count() == len(_codes())
    assert report.created


def test_the_seed_command_reports_what_it_created(capsys: pytest.CaptureFixture[str]) -> None:
    call_command("seed_cms")
    out = capsys.readouterr().out
    assert "created it StandardPage" in out
    call_command("seed_cms")
    assert "0 objects created" in capsys.readouterr().out


# --- Page type rules


def test_home_pages_go_only_under_the_tree_root() -> None:
    root = Page.get_first_root_node()
    home = HomePageFactory.create()
    assert HomePage.can_create_at(root)
    assert not HomePage.can_create_at(home)
    assert StandardPage.can_create_at(home)
    assert not StandardPage.can_create_at(root)


def test_static_pages_can_nest() -> None:
    about = StandardPageFactory.create(title="About us")
    team = StandardPageFactory.create(title="Team", parent=about)
    assert team.get_parent().specific == about
    assert team.url_path.endswith("/about-us/team/")
