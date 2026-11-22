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
    NavigationMenu,
    StandardPage,
)
from apps.core.changeset import ChangeSet
from apps.seed import data
from apps.seed.services import TEST_CONTEXT, seed

pytestmark = pytest.mark.django_db


def shop() -> StandardPage:
    page: StandardPage = StandardPage.objects.get(
        locale=Locale.get_default(), slug=data.SHOP.slug["en"]
    )
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
    assert pages[0].slug == data.SHOP.slug["en"]
    assert pages[2].slug == data.HOW_IT_WORKS.slug["en"]


def test_the_footer_has_a_column_and_a_social_profile(demo_seed: ChangeSet) -> None:
    footer = FooterSettings.for_site(Site.objects.get(is_default_site=True))
    assert footer.columns.exists()
    assert footer.profiles.exists()


def test_a_second_run_changes_nothing(demo_seed: ChangeSet) -> None:
    assert seed(ctx=TEST_CONTEXT).is_empty
