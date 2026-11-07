"""The container health probe against a real server (ARC.5)."""

import pytest
from pytest_django.live_server_helper import LiveServer

from mpmv.healthcheck import probe

pytestmark = pytest.mark.django_db


def test_probe_is_healthy_when_healthz_answers(live_server: LiveServer) -> None:
    assert probe(f"{live_server.url}/healthz", host="testserver") is True


def test_probe_is_unhealthy_on_an_error_status(live_server: LiveServer) -> None:
    assert probe(f"{live_server.url}/no-such-page", host="testserver") is False
