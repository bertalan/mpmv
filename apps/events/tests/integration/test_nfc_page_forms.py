"""I18N.10: the event page types use the NFC admin form (the cms test cannot import events)."""

from typing import Any

import pytest
from wagtail.admin.panels import get_edit_handler

from apps.core.nfc_forms import NFCFormMixin
from apps.events.models import EventIndexPage, EventPage


@pytest.mark.parametrize("model", [EventIndexPage, EventPage])
def test_every_event_page_type_has_the_nfc_form(model: Any) -> None:
    form_class = get_edit_handler(model).get_form_class()
    assert issubclass(form_class, NFCFormMixin)
