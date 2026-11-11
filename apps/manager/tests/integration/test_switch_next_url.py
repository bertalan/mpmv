"""The Manager's language switcher takes its `next` from `switch_next_url`, like the account
layout: the page's own path and query, or "/" for a secret-bearing view (never the raw URL)."""

import re

import pytest
from django.test import Client

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login

pytestmark = pytest.mark.django_db


def test_the_manager_switcher_next_is_the_pages_own_path_and_query(client: Client) -> None:
    verified_login(client, UserFactory.create(permissions=["core.platform_owner"]))
    html = client.get("/manager/platform/?tab=a").content.decode()
    nexts = re.findall(r'name="next" value="([^"]*)"', html)
    assert nexts
    assert set(nexts) == {"/manager/platform/?tab=a"}


def test_the_layout_uses_the_computed_tag_not_the_raw_request_path() -> None:
    from pathlib import Path

    source = Path("apps/manager/templates/manager/layout.html").read_text()
    assert "next_url=next_url" in source
    assert "request.get_full_path" not in source
