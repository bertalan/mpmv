"""E2E (CMS.3): the blog index and a migrated article, and an editor adding an article.

Done when: "Migrated article published". An article created by `import_article` with its legacy
date and author is listed on the blog and readable under the strict CSP (SEC.1); an editor adds
an article in the Wagtail admin and picks its author in the snippet chooser.
"""

import datetime as dt
import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.cms.models import BlogIndexPage, BlogPostPage
from apps.cms.services import ArticleData, import_article
from apps.cms.tests.factories import BlogAuthorFactory, BlogIndexPageFactory
from apps.core.tests.factories import MediaImageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _blog() -> BlogIndexPage:
    index: BlogIndexPage = BlogIndexPageFactory.create(live=False)
    index.save_revision().publish()
    index.refresh_from_db()
    return index


def test_visitor_reads_a_migrated_article_from_the_blog(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    BlogAuthorFactory.create(
        name="Ada Example",
        bio="<p>Ada throws bowls on the wheel.</p>",
        photo=MediaImageFactory.create(),
        photo_alt_text="Ada at the wheel",
    )
    import_article(
        _blog(),
        ArticleData(
            title="A visit to the workshop",
            slug="a-visit-to-the-workshop",
            date=dt.date(2023, 4, 12),
            author="Ada Example",
            summary="How the bowls are made.",
            body=[("paragraph", "<p>We spent a day at the wheel.</p>")],
        ),
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/blog/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Blog")
    expect(page.get_by_text("How the bowls are made.")).to_be_visible()
    shot("blog-index")
    assert run_axe(page) == [], format_violations(run_axe(page))

    page.get_by_role("link", name="A visit to the workshop").click()
    expect(page).to_have_url(re.compile(r"/blog/a-visit-to-the-workshop/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("A visit to the workshop")
    expect(page.locator('time[datetime="2023-04-12"]')).to_be_visible()
    expect(page.get_by_text("Ada Example")).to_be_visible()
    expect(page.get_by_text("We spent a day at the wheel.")).to_be_visible()
    shot("blog-article")
    assert run_axe(page) == [], format_violations(run_axe(page))

    page.get_by_role("link", name="Ada Example").click()
    expect(page).to_have_url(re.compile(r"/blog/author/ada-example/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Ada Example")
    expect(page.get_by_text("Ada throws bowls on the wheel.")).to_be_visible()
    expect(page.get_by_role("link", name="A visit to the workshop")).to_be_visible()
    shot("blog-author")
    assert run_axe(page) == [], format_violations(run_axe(page))

    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_editor_adds_an_article_in_the_admin(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    blog = _blog()
    BlogAuthorFactory.create(name="Ada Example")
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/admin/pages/add/cms/blogpostpage/{blog.pk}/")
    page.locator("#id_title").fill("Spring news")
    page.locator("#id_date").fill("2026-03-01")
    page.get_by_role("button", name="Choose blog author").click()
    modal = page.locator(".modal")
    modal.get_by_role("link", name="Ada Example").click()
    expect(modal).to_be_hidden()
    shot("admin-add-article")
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.get_by_text("Page 'Spring news' created and published.")).to_be_visible()

    post = BlogPostPage.objects.get(title="Spring news")
    assert post.live
    assert post.date == dt.date(2026, 3, 1)
    assert post.author is not None
    assert post.author.name == "Ada Example"
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
