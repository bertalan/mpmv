"""Liveness and readiness probes (OPS.2)."""

from unittest import mock

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_liveness_needs_nothing(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_is_ok_and_unauthenticated(client: Client) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-store" in response["Cache-Control"]


@pytest.mark.parametrize(
    "target",
    [
        "apps.core.health.check_database",
        "apps.core.health.check_cache",
        "apps.core.health.check_storage",
    ],
)
def test_readiness_is_503_when_a_dependency_fails_and_leaks_nothing(
    client: Client, target: str
) -> None:
    with mock.patch(target, side_effect=RuntimeError("secret dsn postgres://u:p@db/x")):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "secret" not in response.content.decode()


def test_readiness_does_not_open_a_session_or_redirect_languages(client: Client) -> None:
    response = client.get("/readyz", headers={"accept-language": "de"})
    assert response.status_code == 200
    assert "sessionid" not in response.cookies


def test_readiness_failure_is_logged_once_per_dependency_per_minute(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    from apps.core import health

    health.reset_log_throttle()
    with (
        mock.patch("apps.core.health.check_cache", side_effect=RuntimeError("x")),
        caplog.at_level("ERROR", logger="apps.core.health"),
    ):
        for _ in range(5):
            assert client.get("/readyz").status_code == 503
    assert len([r for r in caplog.records if r.name == "apps.core.health"]) == 1
    health.reset_log_throttle()
