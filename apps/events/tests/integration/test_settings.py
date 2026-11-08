"""EVT.1: the events settings of a site (spec 15 "Page models", `EventSettings`)."""

import pytest
from django.db import IntegrityError, transaction

from apps.events.models import EventSettings
from apps.events.tests.factories import EventSettingsFactory

pytestmark = pytest.mark.django_db


def test_a_site_has_the_default_threshold_and_retention() -> None:
    settings = EventSettingsFactory.create()
    stored = EventSettings.objects.get(site=settings.site)
    assert stored.moderation_threshold == 3
    assert stored.cancelled_retention_days == 30


def test_a_site_has_one_events_settings_row() -> None:
    settings = EventSettingsFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        EventSettings.objects.create(site=settings.site)
