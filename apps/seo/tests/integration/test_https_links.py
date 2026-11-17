"""I18N.18: on an https Site every canonical and hreflang URL starts with https://."""

import re

import pytest
from django.apps import apps
from django.test import Client
from wagtail.models import Site

from apps.cms.tests.factories import StandardPageFactory

pytestmark = pytest.mark.django_db


def test_canonical_and_hreflang_start_with_https_on_an_https_site(client: Client) -> None:
    Site.objects.filter(is_default_site=True).update(hostname="testserver", port=443)
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    page = StandardPageFactory.create()
    page.save_revision().publish()
    html = client.get(page.url or "/").content.decode()
    hrefs = re.findall(r'<link rel="(?:canonical|alternate)"[^>]*href="([^"]+)"', html)
    assert hrefs
    assert all(h.startswith("https://") for h in hrefs)
