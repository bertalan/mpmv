"""SEO.7 Organization node: schema.org shape (local check, no network) and operator provenance."""

import datetime as dt
import json
import re
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.test import Client

from apps.cms.engine import organization_jsonld
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)

# schema.org Organization: Google requires nothing, recommends these; we require name and url and
# type-check every optional property we emit.
REQUIRED = ("@context", "@type", "name", "url")
KNOWN = {"@context", "@type", "@id", "name", "url", "logo", "sameAs", "contactPoint"}
CONTACT_POINT_KNOWN = {"@type", "contactType", "email", "telephone", "availableLanguage"}


def is_https(value: object) -> bool:
    return (
        isinstance(value, str)
        and urlsplit(value).scheme == "https"
        and bool(urlsplit(value).hostname)
    )


def schema_errors(node: dict[str, Any]) -> list[str]:
    """Violations of the schema.org Organization shape; empty when the node is valid."""
    errors = [f"missing {key}" for key in REQUIRED if not node.get(key)]
    if node.get("@context") != "https://schema.org":
        errors.append("bad @context")
    if node.get("@type") != "Organization":
        errors.append("bad @type")
    errors += [f"unknown property {key}" for key in node if key not in KNOWN]
    errors += [f"empty {key}" for key, value in node.items() if value in ("", [], {}, None)]
    if "url" in node and not urlsplit(str(node["url"])).netloc:
        errors.append("url is not absolute")
    if "logo" in node and not is_https(node["logo"]) and not str(node["logo"]).startswith("http"):
        errors.append("logo is not an absolute URL")
    if "sameAs" in node:
        links = node["sameAs"]
        if not isinstance(links, list) or not all(is_https(link) for link in links):
            errors.append("sameAs must be a list of https URLs")
    if "contactPoint" in node:
        point = node["contactPoint"]
        if point.get("@type") != "ContactPoint":
            errors.append("contactPoint needs @type ContactPoint")
        if not (point.get("email") or point.get("telephone")):
            errors.append("contactPoint needs an email or a telephone")
        if not point.get("contactType"):
            errors.append("contactPoint needs a contactType")
        errors += [
            f"unknown contactPoint property {key}"
            for key in point
            if key not in CONTACT_POINT_KNOWN
        ]
    return errors


def test_full_node_is_valid() -> None:
    node = organization_jsonld(
        "Acme",
        "https://example.org/",
        support_email="help@example.org",
        logo_url="https://example.org/logo.svg",
        profiles=["https://www.wikidata.org/wiki/Q1", "https://example.org/p"],
    )
    assert schema_errors(node) == []


def test_minimal_node_is_valid_and_omits_absent_fields() -> None:
    node = organization_jsonld("Acme", "https://example.org/")
    assert schema_errors(node) == []
    assert set(node) == {"@context", "@type", "name", "url"}


def test_checker_rejects_broken_nodes() -> None:
    good = organization_jsonld("Acme", "https://example.org/", support_email="a@example.org")
    assert schema_errors({**good, "name": ""})
    assert schema_errors({**good, "url": "/relative"})
    assert schema_errors({**good, "sameAs": ["http://example.org/x"]})
    assert schema_errors({**good, "contactPoint": {"@type": "ContactPoint"}})
    assert schema_errors({**good, "colour": "red"})


def test_home_page_node_is_valid_and_values_come_from_the_operator() -> None:
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        legal_name="Operator Legal Name Srl",
        support_email="support@operator.example",
        official_profiles=["https://www.wikidata.org/wiki/Q42", "https://example.org/operator"],
    )
    body = Client().get("/").content.decode()
    scripts = LD.findall(body)
    assert len(scripts) == 1
    node = json.loads(scripts[0])
    assert schema_errors(node) == []
    assert node["name"] == "Operator Legal Name Srl"
    assert node["contactPoint"]["email"] == "support@operator.example"
    assert node["sameAs"] == [
        "https://www.wikidata.org/wiki/Q42",
        "https://example.org/operator",
    ]


def test_node_follows_the_current_operator_version() -> None:
    PlatformOperatorFactory.create(
        valid_from=dt.datetime(2019, 1, 1, tzinfo=dt.UTC),
        valid_to=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        legal_name="Old Operator",
    )
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        legal_name="New Operator",
    )
    node = json.loads(LD.findall(Client().get("/").content.decode())[0])
    assert node["name"] == "New Operator"
    assert "contactPoint" not in node
    assert "sameAs" not in node
    assert "logo" not in node
    assert schema_errors(node) == []
