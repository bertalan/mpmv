"""THM.14 part 2: the ride countdown component renders its markup.

THM.36 moved the script to the CMS block (see tests/integration/test_theme_countdown_script.py),
so the layout no longer loads it.
"""

from datetime import UTC, datetime

import pytest
from django.template import engines
from django.test import RequestFactory

from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db


def test_the_ride_layout_leaves_countdown_js_to_the_countdown_block() -> None:
    template = engines["django"].from_string(
        "{% extends 'layouts/base.html' %}{% block content %}"
        '{% include "components/launch_countdown.html" with title="x" target=target'
        " heading_level=2 only %}{% endblock %}"
    )
    request = RequestFactory().get("/")
    with use_theme("ride"):
        html = template.render({"target": datetime(2031, 1, 1, 9, 0, tzinfo=UTC)}, request)
    assert "data-countdown" in html
    assert "themes/base/js/lazy/countdown.js" not in html
    assert "ride-countdown" in html
