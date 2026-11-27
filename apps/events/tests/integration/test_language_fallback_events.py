"""I18N.21 and Q-72: an event has no URL under a language it lacks, so the CMS fallback never
serves it (`language_fallback = False`)."""

import pytest
from django.test import Client
from wagtail.models import Site

from apps.cms.services import seed_site
from apps.events.models import EventIndexPage
from apps.events.tests.factories import EventIndexPageFactory, EventPageFactory
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db


def test_an_event_never_falls_back(client: Client) -> None:
    seed_site()
    home = Site.objects.get(is_default_site=True).root_page
    index = EventIndexPage.objects.child_of(home).first() or EventIndexPageFactory.create(
        parent=home
    )
    event = EventPageFactory.create(parent=index)
    prefix = language_prefix(other_language())
    assert client.get(f"{prefix}/{index.slug}/{event.slug}/").status_code == 404


def test_the_events_list_opts_out_of_the_fallback() -> None:
    """Every language has its own events list (seeded), so no live URL shows the opt-out."""
    assert EventIndexPage.language_fallback is False
