"""A small public site for the THM.9 tests (integration parity and e2e): one of every content
and system page type, returned as paths."""

import datetime as dt

from django.urls import reverse
from django.utils import timezone
from wagtail.models import Page

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms.models import BlogIndexPage, HomePage, LegalPage, VendorLandingPage
from apps.cms.services import seed_site
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    BlogPostPageFactory,
    LegalPageFactory,
    StandardPageFactory,
    VendorRecruitmentPageFactory,
)
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.core.tests.factories import MediaImageFactory, PlatformOperatorFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory


def _publish(page: Page) -> Page:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def build_content_site(*, posts: int = 3, images: bool = False) -> dict[str, str]:
    """Paths by page type: standard, blog, post, author, legal, legal_version, vendor,
    recruitment, contact. With `images`, the first post has a main image and the author a photo."""
    seed_site()
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=timezone.now() - dt.timedelta(days=1),
        authority_contact_email="authorities@example.com",
        authority_contact_languages=[primary_language()],
        support_email="support@example.com",
        user_contact_url="https://example.com/contact",
        user_contact_languages=[primary_language()],
    )
    home = HomePage.objects.get(locale__language_code=primary_language())
    standard = _publish(StandardPageFactory.create(title="About the shop", live=False))
    blog: BlogIndexPage = BlogIndexPageFactory.create(live=False)
    _publish(blog)
    author = BlogAuthorFactory.create(name="Ada Example")
    if images:
        author.photo = MediaImageFactory.create(file__width=2000, file__height=1500)
        author.photo_alt_text = "Ada at the wheel"
        author.save()
    for number in range(posts):
        post = BlogPostPageFactory.create(
            title=f"Workshop visit {number}",
            slug=f"workshop-visit-{number}",
            parent=blog,
            author=author,
            date=dt.date(2026, 3, 1 + number),
            live=False,
            **(
                {
                    "image": MediaImageFactory.create(file__width=2000, file__height=1500),
                    "image_alt_text": "A workshop bench",
                }
                if images and number == 0
                else {}
            ),
        )
        _publish(post)
    legal: LegalPage = LegalPageFactory.create(title="Terms of sale", live=False)
    _publish(legal)
    legal.effective_date = dt.date(2027, 6, 1)
    legal.body = [("paragraph", "<p>Newer terms.</p>")]
    _publish(legal)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=product,
        title="Acme bag",
        slug="acme-bag",
        language=primary_language(),
        status=TranslationStatus.PUBLISHED,
    )
    landing = VendorLandingPage(
        vendor=vendor,
        title="Our workshop",
        slug="our-workshop",
        body=[("paragraph", "<p>Craft.</p>")],
    )
    home.add_child(instance=landing)
    _publish(landing)
    recruitment = _publish(VendorRecruitmentPageFactory.create(title="Sell with us", live=False))
    return {
        "standard": standard.url,
        "blog": blog.url,
        "post": f"{blog.url}workshop-visit-0/",
        "author": f"{blog.url}author/ada-example/",
        "legal": legal.url,
        "legal_version": f"{legal.url}versions/1/",
        "vendor": landing.url,
        "recruitment": recruitment.url,
        "contact": reverse("dsa_contact_points"),
    }
