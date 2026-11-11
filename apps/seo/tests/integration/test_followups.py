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


def test_organization_node_has_logo_and_profiles_wikidata_first() -> None:
    from apps.cms.engine import organization_jsonld

    node = organization_jsonld(
        "Acme",
        "https://example.org/",
        logo_url="https://example.org/logo.svg",
        profiles=[
            "https://example.org/p",
            "http://insecure.example/x",
            "https://user:pw@example.org/y",
            "https://www.wikidata.org/wiki/Q1",
            "https://example.org/p",
        ],
    )
    assert node["logo"] == "https://example.org/logo.svg"
    assert node["sameAs"] == ["https://www.wikidata.org/wiki/Q1", "https://example.org/p"]


def test_no_logo_no_profiles_no_fields() -> None:
    from apps.cms.engine import organization_jsonld

    node = organization_jsonld("Acme", "https://example.org/")
    assert "logo" not in node
    assert "sameAs" not in node


def test_operator_refuses_bad_profiles() -> None:
    from django.core.exceptions import ValidationError

    from apps.core.models import PlatformOperator

    for bad in (["http://x.example/a"], ["https://u:p@x.example/a"], ["https://x.example/a"] * 2):
        op = PlatformOperatorFactory.build(official_profiles=bad)
        with pytest.raises(ValidationError) as caught:
            PlatformOperator.clean(op)
        assert "official_profiles" in caught.value.message_dict


def test_operator_refuses_logo_outside_logos_collection() -> None:
    from django.core.exceptions import ValidationError

    from apps.core.models import MediaImage
    from apps.core.tests.factories import MediaImageFactory

    image = MediaImageFactory.create()
    assert isinstance(image, MediaImage)
    op = PlatformOperatorFactory.build(logo=image)
    with pytest.raises(ValidationError) as caught:
        op.clean()
    assert "logo" in caught.value.message_dict


def test_profiles_dedupe_normalised_and_wikidata_first_both_hosts() -> None:
    from apps.cms.engine import profile_links

    links = profile_links(
        [
            "https://Example.org/a/",
            "https://example.org/a",
            "https://wikidata.org/wiki/Q2",
            "https://bücher.example/x",
            "https://xn--bcher-kva.example/x/",
            "https://www.wikidata.org/wiki/Q1",
        ]
    )
    assert links == [
        "https://wikidata.org/wiki/Q2",
        "https://www.wikidata.org/wiki/Q1",
        "https://example.org/a",
        "https://xn--bcher-kva.example/x",
    ]


@override_settings(ALLOWED_HOSTS=["*"])
def test_url_and_logo_ignore_the_host_header_and_a_missing_file() -> None:
    from wagtail.models import Collection

    from apps.core.tests.factories import MediaImageFactory

    logo = MediaImageFactory.create(collection=Collection.objects.get(svg_policy__isnull=False))
    operator(logo=logo, official_profiles=["https://example.org/p?a=1&b=</script>"])
    site = default_site()
    response = Client().get("/", HTTP_HOST="evil.example")
    body = response.content.decode()
    assert "evil.example" not in body
    assert "</script>?" not in body
    node = json.loads(LD.findall(body)[0])
    assert node["url"].startswith(site.root_url)
    assert node["logo"].startswith(site.root_url)
    logo.file.storage.delete(logo.file.name)  # the file is gone from storage: still no crash
    assert Client().get("/", HTTP_HOST="evil.example").status_code == 200
    type(logo).objects.filter(pk=logo.pk).update(file="")  # no file behind the field
    node = json.loads(LD.findall(Client().get("/", HTTP_HOST="evil.example").content.decode())[0])
    assert "logo" not in node


ADDRESS = "main site address"


def address_messages() -> list[str]:
    return [m for m in golive_problems(["en"]) if ADDRESS in m]


@pytest.mark.parametrize(
    ("hostname", "port"),
    [("localhost", 443), ("127.0.0.1", 443), ("::1", 443), ("shop.local", 443), ("a.test", 443)],
)
@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_local_site_address_is_reported(hostname: str, port: int) -> None:
    Site.objects.filter(is_default_site=True).update(hostname=hostname, port=port)
    (message,) = address_messages()
    assert "Settings > Sites" in message


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_non_https_site_address_is_reported() -> None:
    Site.objects.filter(is_default_site=True).update(hostname="shop.example.org", port=80)
    assert len(address_messages()) == 1


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_real_https_site_address_is_not_reported() -> None:
    Site.objects.filter(is_default_site=True).update(hostname="shop.example.org", port=443)
    assert address_messages() == []


@override_settings(FORCE_NOINDEX=True)
def test_site_address_not_reported_on_the_floor() -> None:
    Site.objects.filter(is_default_site=True).update(hostname="localhost", port=80)
    assert address_messages() == []
