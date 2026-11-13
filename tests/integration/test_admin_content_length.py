"""Regression (ARC.32): the Wagtail admin pages keep a true Content-Length.

The nonce middleware strips the help assets from a visitor's page; a stale Content-Length made
the browser wait for bytes that never came (every admin page timed out on `load`)."""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_wagtail_login_content_length_matches_body(client: Client) -> None:
    response = client.get("/admin/login/")
    assert response.status_code == 200
    assert b"admin-help" not in response.content
    assert int(response.headers["Content-Length"]) == len(response.content)
