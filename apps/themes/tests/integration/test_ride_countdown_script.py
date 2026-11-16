"""THM.14 part 2: the ride base layout loads countdown.js when the page has a launch countdown."""

from datetime import UTC, datetime

import pytest
from django.template import engines
from django.test import RequestFactory

from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db


def test_the_ride_base_loads_countdown_js_for_the_launch_countdown() -> None:
    template = engines["django"].from_string(
        "{% extends 'layouts/base.html' %}{% block content %}"
        '{% include "components/launch_countdown.html" with title="x" target=target'
        " heading_level=2 only %}{% endblock %}"
    )
    request = RequestFactory().get("/")
    with use_theme("ride"):
        html = template.render({"target": datetime(2031, 1, 1, 9, 0, tzinfo=UTC)}, request)
    assert "data-countdown" in html
    assert "themes/base/js/countdown.js" in html
    assert "ride-countdown" in html
