"""factory_boy factories for the CMS (CMS.1). Synthetic values only.

Wagtail pages live in a tree (django-treebeard), so `.create()` adds the page under `parent`
with `add_child` and publishes nothing: pass `live=False` to get a draft. A language has one
`HomePage` (the migration creates the primary language's), so `HomePageFactory` takes the first
configured language without one unless `locale` is given, and goes under the tree root. A
`StandardPage` goes under the primary language's home unless `parent` is given.
`wagtail-factories` is not used: it imports Wagtail's image and document models at import
time, and CMS.1 installs neither app.
"""

from typing import Any
from unittest import mock

import factory
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.utils.text import slugify
from factory.django import DjangoModelFactory
from wagtail.models import Locale, Page

from apps.cms.models import HomePage, NavigationMenu, StandardPage
from apps.core.i18n import configured_languages


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
