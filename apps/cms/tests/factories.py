"""factory_boy factories for the CMS (CMS.1). Synthetic values only.

Wagtail pages live in a tree (django-treebeard), so `.create()` adds the page under `parent`
with `add_child` and publishes nothing: pass `live=False` to get a draft. A language has one
`HomePage` (the migration creates the primary language's), so `HomePageFactory` takes the first
configured language without one unless `locale` is given, and goes under the tree root. A
`StandardPage` goes under the primary language's home unless `parent` is given.
`wagtail-factories` is not used: it imports Wagtail's image and document models at import
time, and CMS.1 installs neither app.
"""

import datetime as dt
from typing import Any
from unittest import mock

import factory
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.utils import timezone
from django.utils.text import slugify
from factory.django import DjangoModelFactory
from wagtail.models import Locale, Page

from apps.cms.models import (
    BlogAuthor,
    BlogIndexPage,
    BlogPostPage,
    HomePage,
    LegalPage,
    LegalPageReview,
    LegalPageVersion,
    NavigationMenu,
    StandardPage,
    TermLandingPage,
    VendorLandingPage,
    VendorRecruitmentPage,
)
from apps.core.i18n import configured_languages
from apps.vendors.models import Vendor


def _locale_without_home() -> Locale:
    taken = set(HomePage.objects.values_list("locale__language_code", flat=True))
    for code in configured_languages():
        if code not in taken:
            locale: Locale = Locale.objects.get(language_code=code)
            return locale
    raise LookupError("Every configured language has a home page: pass `locale`")


def _primary_home() -> HomePage:
    home: HomePage | None = HomePage.objects.filter(locale=Locale.get_default()).first()
    return home if home is not None else HomePageFactory.create()


def _unsaved_content_type(model: type[Model], for_concrete_model: bool = True) -> ContentType:
    return ContentType(app_label=model._meta.app_label, model=str(model._meta.model_name))


class _InTree:
    """Saves pages with treebeard's `add_child` under the `parent` declaration.

    Defaults that need the database (the parent, a home page's locale) are filled in
    `_create` only, so `.build()` stays off the database.
    """

    @classmethod
    def _build(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        kwargs.pop("parent", None)
        # A new Page looks up its ContentType row on init; `.build()` stays off the database
        # with an unsaved one, so a built page can be rendered or validated but never saved.
        with mock.patch.object(ContentType.objects, "get_for_model", _unsaved_content_type):
            return model_class(*args, **kwargs)

    @classmethod
    def _create(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        parent = kwargs.pop("parent", None) or cls._default_parent()
        page = model_class(*args, **kwargs)
        parent.add_child(instance=page)
        return page

    @classmethod
    def _default_parent(cls) -> Page:
        return Page.get_first_root_node()


class HomePageFactory(_InTree, DjangoModelFactory[HomePage]):
    class Meta:
        model = HomePage

    title = factory.Sequence(lambda n: f"Home {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None

    @classmethod
    def _create(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        kwargs.setdefault("locale", _locale_without_home())
        return super()._create(model_class, *args, **kwargs)


class StandardPageFactory(_InTree, DjangoModelFactory[StandardPage]):
    class Meta:
        model = StandardPage

    title = factory.Sequence(lambda n: f"Page {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Sample text.</p>")])
    image = None
    image_alt_text = ""
    image_caption = ""

    @classmethod
    def _default_parent(cls) -> Page:
        return _primary_home()


class TermLandingPageFactory(_InTree, DjangoModelFactory[TermLandingPage]):
    """A term landing page (CMS.4) under the primary language's home, for a new term of a
    taxonomy with term pages unless `term` is given."""

    class Meta:
        model = TermLandingPage

    title = factory.Sequence(lambda n: f"Term guide {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    term = factory.SubFactory(
        "apps.catalog.tests.factories.TermFactory",
        taxonomy=factory.SubFactory(
            "apps.catalog.tests.factories.TaxonomyFactory", has_landing=True
        ),
    )
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Sample intro.</p>")])

    @classmethod
    def _default_parent(cls) -> Page:
        return _primary_home()


class VendorLandingPageFactory(_InTree, DjangoModelFactory[VendorLandingPage]):
    """A vendor landing page (CMS.4) under the primary language's home, for an active vendor
    unless `vendor` is given. A page outlives its vendor's active status (a suspension), so the
    factory makes one for a vendor in any status."""

    class Meta:
        model = VendorLandingPage

    title = factory.Sequence(lambda n: f"Our story {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    vendor = factory.SubFactory(
        "apps.vendors.tests.factories.VendorFactory", status=Vendor.Status.ACTIVE
    )
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Sample story.</p>")])

    @classmethod
    def _create(cls, model_class: type[Page], *args: Any, **kwargs: Any) -> Page:
        with mock.patch("apps.cms.models.has_public_storefront", return_value=True):
            return super()._create(model_class, *args, **kwargs)

    @classmethod
    def _default_parent(cls) -> Page:
        return _primary_home()


class NavigationMenuFactory(DjangoModelFactory[NavigationMenu]):
    """A menu of the primary language unless `locale` is given: header first, then footer.

    A language has one menu per location, so a third menu needs another `locale`. `links` is a
    list of ("page", {"page": page, "label": ""}) or ("external", {"url": ..., "label": ...}).
    """

    class Meta:
        model = NavigationMenu

    location = factory.Sequence(lambda n: NavigationMenu.Location.values[n % 2])
    links = factory.LazyFunction(
        lambda: [("external", {"url": "https://example.com/help/", "label": "Help"})]
    )

    @classmethod
    def _create(cls, model_class: type[NavigationMenu], *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("locale", Locale.get_default())
        return model_class._default_manager.create(*args, **kwargs)


class LegalPageFactory(_InTree, DjangoModelFactory[LegalPage]):
    """A legal page under the primary language's home. Creating it records no version: a
    version is recorded when the page is published (`page.save_revision().publish()`)."""

    class Meta:
        model = LegalPage

    title = factory.Sequence(lambda n: f"Terms {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    kind = LegalPage.Kind.TERMS
    effective_date = dt.date(2027, 1, 1)
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Sample terms.</p>")])

    @classmethod
    def _default_parent(cls) -> Page:
        return _primary_home()


class LegalPageVersionFactory(DjangoModelFactory[LegalPageVersion]):
    """A recorded version, numbered after the page's last one (normally `record_legal_version`
    writes these when a page is published)."""

    class Meta:
        model = LegalPageVersion

    page = factory.SubFactory(LegalPageFactory)
    title = factory.LazyAttribute(lambda version: version.page.title)
    # A built page has no locale yet (`.build()` stays off the database).
    language_code = factory.LazyAttribute(
        lambda version: (
            version.page.locale.language_code if version.page.locale_id else settings.LANGUAGE_CODE
        )
    )
    effective_date = factory.LazyAttribute(lambda version: version.page.effective_date)
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Sample terms.</p>")])
    published_at = factory.LazyFunction(timezone.now)
    published_by = "system"

    @classmethod
    def _create(cls, model_class: type[LegalPageVersion], *args: Any, **kwargs: Any) -> Any:
        if "number" not in kwargs:
            kwargs["number"] = kwargs["page"].versions.count() + 1
        return model_class._default_manager.create(*args, **kwargs)


class LegalPageReviewFactory(DjangoModelFactory[LegalPageReview]):
    """A review of a recorded version (I18N.9); normally `services.record_legal_review`."""

    class Meta:
        model = LegalPageReview

    version = factory.SubFactory(LegalPageVersionFactory)
    stage = LegalPageReview.Stage.COUNSEL_CHECK
    reviewer = "Example Counsel"
    reviewed_on = dt.date(2026, 10, 1)
    recorded_at = factory.LazyFunction(timezone.now)
    recorded_by = "system"


class VendorRecruitmentPageFactory(_InTree, DjangoModelFactory[VendorRecruitmentPage]):
    """A recruitment page under the primary language's home, with two plans and a FAQ.

    Titles are numbered so two pages fit under one home: pass `title="Sell with us"` for the
    page at `/sell-with-us/`."""

    class Meta:
        model = VendorRecruitmentPage

    title = factory.Sequence(lambda n: f"Sell with us {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    intro = factory.LazyFunction(lambda: [("paragraph", "<p>Open your shop.</p>")])
    plans = factory.LazyFunction(
        lambda: [
            (
                "plan",
                {
                    "name": "Starter",
                    "price": "No monthly fee",
                    "summary": "For makers who are starting out.",
                    "features": ["Up to 50 products", "Standard commission"],
                    "highlighted": False,
                },
            ),
            (
                "plan",
                {
                    "name": "Workshop",
                    "price": "Monthly fee",
                    "summary": "For established workshops.",
                    "features": ["Unlimited products", "Reduced commission"],
                    "highlighted": True,
                },
            ),
        ]
    )
    faq = factory.LazyFunction(
        lambda: [
            (
                "question",
                {"question": "Who can sell?", "answer": "<p>Independent makers.</p>"},
            )
        ]
    )
    cta_label = "Apply now"

    @classmethod
    def _default_parent(cls) -> Page:
        return _primary_home()


class BlogAuthorFactory(DjangoModelFactory[BlogAuthor]):
    """An author of the default language unless `locale` is given; the slug follows the name.
    Translate one with `author.copy_for_translation(locale)`."""

    class Meta:
        model = BlogAuthor
        django_get_or_create = ("name", "locale")

    name = factory.Sequence(lambda n: f"Author {n}")
    bio = "<p>Writes about the workshops.</p>"

    @classmethod
    def _create(cls, model_class: type[BlogAuthor], *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("locale", Locale.get_default())
        return super()._create(model_class, *args, **kwargs)  # type: ignore[no-untyped-call]


class BlogIndexPageFactory(_InTree, DjangoModelFactory[BlogIndexPage]):
    """A language's blog (CMS.3), `/blog/` under its home: the primary language's first, then
    the first language whose home has no blog yet (one blog per home)."""

    class Meta:
        model = BlogIndexPage

    title = "Blog"
    slug = "blog"
    parent = None
    intro = "<p>News from the workshops.</p>"

    @classmethod
    def _default_parent(cls) -> Page:
        home = _primary_home()
        if not BlogIndexPage.objects.child_of(home).exists():
            return home
        for other in HomePage.objects.exclude(pk=home.pk):
            if not BlogIndexPage.objects.child_of(other).exists():
                return other
        return HomePageFactory.create()


class BlogPostPageFactory(_InTree, DjangoModelFactory[BlogPostPage]):
    """An article under `parent`, by default the primary language's blog (created if missing).
    Pass `live=False` for a draft and publish it with `page.save_revision().publish()`."""

    class Meta:
        model = BlogPostPage

    title = factory.Sequence(lambda n: f"Article {n}")
    slug = factory.LazyAttribute(lambda page: slugify(page.title))
    parent = None
    date = dt.date(2026, 3, 1)
    summary = "A short summary."
    body = factory.LazyFunction(lambda: [("paragraph", "<p>Article text.</p>")])
    image = None
    image_alt_text = ""
    image_caption = ""

    @classmethod
    def _default_parent(cls) -> Page:
        blog: BlogIndexPage | None = BlogIndexPage.objects.child_of(_primary_home()).first()
        return blog if blog is not None else BlogIndexPageFactory.create()
