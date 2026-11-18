"""ARC.37: `help-centre` is reserved for the customer help; no root-level page can take it."""

import pytest
from django.core.exceptions import ValidationError
from wagtail.models import Page

from apps.cms import engine
from apps.cms.checks import check_reserved_root_slugs
from apps.cms.models import HomePage, StandardPage
from apps.cms.tests.factories import HomePageFactory, StandardPageFactory

pytestmark = pytest.mark.django_db


def _home() -> HomePage:
    page = HomePageFactory.create()
    assert isinstance(page, HomePage)
    return page


def test_slug_availability_refuses_the_reserved_slug_under_a_home() -> None:
    home = _home()
    assert not Page._slug_is_available("help-centre", home)
    assert Page._slug_is_available("help", home)


def test_the_reserved_slug_is_free_deeper_in_the_tree() -> None:
    child = StandardPageFactory.create(parent=_home(), slug="about")
    assert Page._slug_is_available("help-centre", child)


def test_an_existing_page_cannot_be_renamed_to_the_reserved_slug() -> None:
    page = StandardPageFactory.create(parent=_home(), slug="about")
    page.slug = "help-centre"
    with pytest.raises(ValidationError) as caught:
        page.clean()
    assert "reserved" in str(caught.value)


def test_the_page_form_shows_the_reserved_message() -> None:
    home = _home()
    form_class = StandardPage.get_edit_handler().get_form_class()
    form = form_class(
        {"title": "Help", "slug": "help-centre", "body-count": "0"},
        instance=StandardPage(),
        parent_page=home,
    )
    assert not form.is_valid()
    assert any("reserved" in e for e in form.errors.get("slug", [])) or any(
        "reserved" in e for e in form.non_field_errors()
    )
    assert not any("already in use" in e for e in form.errors.get("slug", []))


def test_the_check_flags_a_live_page_with_the_reserved_slug() -> None:
    assert check_reserved_root_slugs(None) == []
    page = StandardPageFactory.create(parent=_home(), slug="about")
    Page.objects.filter(pk=page.pk).update(slug="help-centre")  # past the validation
    assert [e.id for e in check_reserved_root_slugs(None)] == ["cms.E004"]


def test_no_page_of_the_seeded_tree_uses_a_reserved_slug() -> None:
    assert not Page.objects.filter(depth=3, slug__in=engine.RESERVED_ROOT_SLUGS).exists()
