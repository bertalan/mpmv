"""ING.4: mappings with real foreign keys, loaded through the audited import service, and the
extraction service on top of them (50 sample products, PostgreSQL)."""

import json
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.catalog.models import Category, Term
from apps.catalog.tests.factories import CategoryFactory, TermFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import services
from apps.ingest.models import MappingTable
from apps.ingest.tests.factories import MappingTableFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ingest", source="admin", ip="127.0.0.1")
FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "extraction_samples.json").read_text()
)


def fixture_specs(types: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for word, code in FIXTURE["types"].items():
        specs.append(
            {"kind": "product_type", "key": word, "value": code, **(types or {}).get(word, {})}
        )
    for name, info in FIXTURE["materials"].items():
        specs.append(
            {"kind": "material", "key": name, "aliases": info["variants"], "value": info["it"]}
        )
    for word, english in FIXTURE["colors"].items():
        specs.append({"kind": "color", "key": word, "value": english})
    specs.append({"kind": "country", "key": "Italy", "aliases": ["Italia"], "value": "it"})
    return specs


def test_import_creates_audited_mappings_and_a_rerun_changes_nothing() -> None:
    vendor = VendorFactory.create()
    specs = fixture_specs()
    first = services.import_mappings(vendor, specs, ctx=CTX)
    assert first.summary()["create"] == len(specs)
    assert MappingTable.objects.filter(vendor=vendor).count() == len(specs)
    assert AuditLog.objects.filter(action="ingest.mapping.created").count() == len(specs)
    again = services.import_mappings(vendor, specs, ctx=CTX)
    assert again.summary() == {"create": 0, "update": 0, "delete": 0}
    assert MappingTable.objects.filter(vendor=vendor).count() == len(specs)


def test_import_dry_run_reports_the_changes_and_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = services.import_mappings(vendor, fixture_specs(), ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == len(fixture_specs())
    assert not MappingTable.objects.exists()
    assert not AuditLog.objects.filter(action="ingest.mapping.created").exists()


def test_a_mapping_points_at_a_category_and_a_term_by_foreign_key() -> None:
    vendor = VendorFactory.create()
    category = CategoryFactory.create()
    term = TermFactory.create()
    services.import_mappings(
        vendor,
        [
            {
                "kind": "category",
                "key": "Side bags",
                "target": {"model": "catalog.category", "public_id": str(category.public_id)},
            },
            {
                "kind": "term",
                "key": "Touring",
                "target": {"model": "catalog.term", "public_id": str(term.public_id)},
            },
        ],
        ctx=CTX,
    )
    found = services.lookup_mapping(vendor, MappingTable.Kind.CATEGORY, "side  bags")
    assert found is not None
    assert isinstance(found.target, Category)
    assert found.target == category
    term_row = services.lookup_mapping(vendor, MappingTable.Kind.TERM, "touring")
    assert term_row is not None
    assert isinstance(term_row.target, Term)
    assert term_row.target_value == ""


def test_a_failed_import_is_all_or_nothing() -> None:
    vendor = VendorFactory.create()
    specs = [{"kind": "color", "key": "red", "value": "red"}, {"kind": "color", "key": "empty"}]
    with pytest.raises(ValidationError):
        services.import_mappings(vendor, specs, ctx=CTX)
    assert not MappingTable.objects.exists()


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "nonsense", "key": "a", "value": "b"},
        {"kind": "color", "key": "  ", "value": "b"},
        {"kind": "term", "key": "a", "target": {"model": "accounts.user", "public_id": "x"}},
        {
            "kind": "term",
            "key": "a",
            "target": {"model": "catalog.term", "public_id": "not-a-uuid"},
        },
    ],
)
def test_import_refuses_bad_specs(spec: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        services.import_mappings(VendorFactory.create(), [spec], ctx=CTX)


def test_extraction_reproduces_the_fifty_samples_from_database_mappings() -> None:
    vendor = VendorFactory.create()
    services.import_mappings(vendor, fixture_specs(), ctx=CTX)
    loaded = services.load_extraction_rules(vendor)
    for sample in FIXTURE["samples"]:
        got = services.extract_product(
            vendor, title=sample["title"], description=sample["description"], loaded=loaded
        )
        expected = dict(sample["expected"])
        if "product_type" in expected:
            expected["product_type"] = {
                "key": next(
                    k for k, v in FIXTURE["types"].items() if v == expected["product_type"]
                ),
                "value": expected["product_type"],
            }
        for plural, singular in (("materials", "material"), ("colors", "colour")):
            if plural in expected:
                expected[singular] = ", ".join(expected[plural])
        assert got == expected, sample["title"]


def test_product_type_can_be_a_term_target() -> None:
    vendor = VendorFactory.create()
    term = TermFactory.create()
    target = {"model": "catalog.term", "public_id": str(term.public_id)}
    services.import_mappings(
        vendor, fixture_specs({"zaino": {"value": None, "target": target}}), ctx=CTX
    )
    got = services.extract_product(vendor, title="Zaino modello Alpha", description="")
    assert got["product_type"] == {
        "key": "zaino",
        "target": "catalog.term",
        "public_id": str(term.public_id),
    }


def test_cat22_fields_are_optional_import_fields() -> None:
    vendor = VendorFactory.create()
    services.import_mappings(vendor, fixture_specs(), ctx=CTX)
    got = services.extract_product(
        vendor,
        title="Zaino nero",
        description="Materiali: pelle liscia. Misure 30 x 20 x 10 cm. Made in Italia",
    )
    assert got["length_mm"] == 300
    assert got["width_mm"] == 200
    assert got["height_mm"] == 100
    assert got["country_of_origin"] == "IT"
    assert got["material"] == "Pelle"
    assert got["colour"] == "black"
    bare = services.extract_product(vendor, title="Zaino", description="")
    assert not {"length_mm", "material", "colour", "country_of_origin"} & set(bare)


def test_a_vendor_mapping_overrides_the_platform_one_and_other_vendors_are_isolated() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    MappingTableFactory.create(vendor=None, kind="color", key="rosso", target_value="red")
    MappingTableFactory.create(vendor=vendor, kind="color", key="rosso", target_value="crimson")
    MappingTableFactory.create(vendor=other, kind="color", key="verde", target_value="green")
    mine = services.extract_product(vendor, title="Borsa rosso verde", description="")
    assert mine["colour"] == "crimson"
    theirs = services.extract_product(other, title="Borsa rosso verde", description="")
    assert theirs["colour"] == "red, green"
