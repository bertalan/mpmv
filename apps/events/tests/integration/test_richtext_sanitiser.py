"""CMS.12: the sanitiser covers the events app's rich text too (spec 10 §10.2, spec 15)."""

import pytest

from apps.cms.tests.factories import HomePageFactory
from apps.events.models import EventIndexPage
from tests import hostile_input as hi

pytestmark = pytest.mark.django_db


def test_an_event_index_intro_is_stored_without_active_markup() -> None:
    hostile = hi.HTML[1] + hi.HTML[5] + hi.HTML[7] + "<p>kept</p>"
    home = HomePageFactory.create()
    index = home.add_child(instance=EventIndexPage(title="Events", slug="events", intro=hostile))

    stored = EventIndexPage.objects.get(pk=index.pk).intro

    assert hi.active_markup(stored) == []
    assert "kept" in stored
