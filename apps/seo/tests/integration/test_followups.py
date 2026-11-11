"""SEO.8 follow-ups: go-live indexing warning and the operator Organization JSON-LD."""

import datetime as dt
import json
import re

import pytest
from django.test import Client, override_settings
from wagtail.models import Site

from apps.cms.services import golive_problems
from apps.core.tests.factories import PlatformOperatorFactory
from apps.seo import services

pytestmark = pytest.mark.django_db

LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
WARNING = "Search engine indexing is still off"


def indexing_messages() -> list[str]:
    return [m for m in golive_problems(["en"]) if WARNING in m]


def operator(**kwargs: object) -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), **kwargs
    )


def default_site() -> Site:
    site: Site = Site.objects.get(is_default_site=True)
    return site


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_live_with_indexing_off_is_reported() -> None:
    (message,) = indexing_messages()
    assert "Settings" in message


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_live_with_indexing_on_is_not_reported() -> None:
    from apps.accounts.tests.factories import UserFactory

    services.set_site_indexing(UserFactory.create(superuser=True), default_site(), True, ctx=None)
    assert indexing_messages() == []


@override_settings(FORCE_NOINDEX=True)
def test_stage_floor_is_not_reported() -> None:
    assert indexing_messages() == []


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_home_page_has_one_organization_node() -> None:
    operator(legal_name="Acme </script><b>x</b> Srl", support_email="help@example.org")
    response = Client().get("/")
    body = response.content.decode()
    nodes = [json.loads(raw) for raw in LD.findall(body)]
    orgs = [n for n in nodes if n.get("@type") == "Organization"]
    assert len(orgs) == 1
    org = orgs[0]
    assert org["name"] == "Acme </script><b>x</b> Srl"
    assert org["url"].startswith("http")
    assert org["contactPoint"]["email"] == "help@example.org"
    assert "sameAs" not in org
    assert "<b>x</b>" not in body


def test_home_page_without_operator_has_no_organization() -> None:
    body = Client().get("/").content.decode()
    assert "Organization" not in body
