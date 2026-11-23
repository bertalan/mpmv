"""THM.23 1/3: the demo seed gives the shop page a full home body, a blog, the header menu and
the footer, and a second run changes nothing.

The CMS.19 vendor-recruitment home stays as it is (test_recruitment_home.py).
"""

import pytest
from django.test import Client
from wagtail.models import Locale, Site

from apps.cms.models import (
    BlogIndexPage,
    BlogPostPage,
    FooterSettings,
    HomePage,
    NavigationMenu,
    StandardPage,
)
from apps.cms.seed import seed_text
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.seed import data
from apps.seed.services import TEST_CONTEXT, seed

pytestmark = pytest.mark.django_db


def shop_slug() -> str:
    """The shop slug in the primary language (English text where it has none)."""
    return data.SHOP.slug.get(primary_language()) or data.SHOP.slug["en"]


def shop() -> StandardPage:
    page: StandardPage = StandardPage.objects.get(locale=Locale.get_default(), slug=shop_slug())
    return page


def test_the_shop_page_has_at_least_six_sections(demo_seed: ChangeSet) -> None:
    kinds = [block.block_type for block in shop().body]
    assert len(kinds) >= 6
    assert {"hero", "product_grid", "strip", "cta"} <= set(kinds)
    hero = next(block.value for block in shop().body if block.block_type == "hero")
    assert hero["layout"] == "slider"
    assert len(hero["slides"]) >= 2


def test_the_blog_is_reachable_and_the_home_teases_it(demo_seed: ChangeSet, client: Client) -> None:
    blog = BlogIndexPage.objects.get(locale=Locale.get_default())
    assert client.get(blog.url).status_code == 200
    posts = BlogPostPage.objects.child_of(blog).live()
    assert posts.count() >= 2
    for post in posts:
        assert client.get(post.url).status_code == 200
    teaser = [
        block.value
        for block in shop().body
        if block.block_type == "strip" and block.value["cards"][0].block_type == "page"
    ]
    assert teaser
    assert client.get(shop().url).status_code == 200


def test_the_header_menu_links_shop_blog_and_about(demo_seed: ChangeSet) -> None:
    menu = NavigationMenu.objects.get(locale=Locale.get_default(), location="header")
    pages = [link.value["page"].specific for link in menu.links]
    assert [type(page) for page in pages][:3] == [StandardPage, BlogIndexPage, StandardPage]
    assert pages[0].slug == shop_slug()


def _header() -> NavigationMenu:
    locale = Locale.get_default()
    return NavigationMenu.objects.get(locale=locale, location="header")


def _default_menu(about: StandardPage) -> list[tuple[str, dict[str, object]]]:
    return [("page", {"page": about, "label": ""})]


def _reset_to(links: list[tuple[str, dict[str, object]]]) -> NavigationMenu:
    menu = _header()
    menu.links = links
    menu.save()
    return menu


def _about() -> StandardPage:
    locale = Locale.get_default()
    home = HomePage.objects.get(locale=locale, depth=2)
    page: StandardPage = StandardPage.objects.child_of(home).get(
        slug=seed_text(locale.language_code).about.slug
    )
    return page


def test_the_untouched_cms1_menu_is_rewritten(demo_seed: ChangeSet) -> None:
    _reset_to(_default_menu(_about()))
    seed(ctx=TEST_CONTEXT)
    assert len(_header().links) >= 3


@pytest.mark.parametrize("edit", ["other page", "label", "no page"])
def test_an_edited_single_link_menu_is_left_alone(demo_seed: ChangeSet, edit: str) -> None:
    other = shop()
    links: list[tuple[str, dict[str, object]]]
    if edit == "other page":
        links = _default_menu(other)
    elif edit == "label":
        links = [("page", {"page": _about(), "label": "Who we are"})]
    else:
        links = [("page", {"page": None, "label": ""})]
    before = _reset_to(links).links.get_prep_value()
    seed(ctx=TEST_CONTEXT)
    after = _header().links.get_prep_value()
    assert [{k: v for k, v in b.items() if k != "id"} for b in after] == [
        {k: v for k, v in b.items() if k != "id"} for b in before
    ]


def test_the_footer_has_a_column_and_a_social_profile(demo_seed: ChangeSet) -> None:
    footer = FooterSettings.for_site(Site.objects.get(is_default_site=True))
    assert footer.columns.exists()
    assert footer.profiles.exists()


def test_a_second_run_changes_nothing(demo_seed: ChangeSet) -> None:
    assert seed(ctx=TEST_CONTEXT).is_empty
