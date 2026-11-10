"""SHP.13: the `shipping_config` command, a thin adapter over `apps.shipping.config_io` for staff
and for the SHP.5 legacy import: export, import (dry run unless --apply), templates."""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from apps.core.models import AuditLog
from apps.shipping import config_format as fmt
from apps.shipping.config_io import export_config
from apps.shipping.models import ShippingTemplate, ShippingZone
from apps.shipping.tests.factories import CarrierFactory, ShippingMethodFactory, ShippingZoneFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def run(*args: str) -> str:
    out = StringIO()
    call_command("shipping_config", *args, stdout=out)
    return out.getvalue()


def vendor_file(tmp_path: Path, name: str = "config.json") -> tuple[Path, dict[str, object]]:
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(vendor=vendor, code="home", countries=["FR"])
    ShippingMethodFactory.create(
        vendor=vendor, zone=zone, carrier=CarrierFactory.create(code="main")
    )
    document = export_config(vendor)
    path = tmp_path / name
    path.write_text(fmt.dump_csv(document) if name.endswith(".csv") else fmt.dump_json(document))
    return path, document


def test_export_prints_a_vendor_configuration_as_json_or_csv() -> None:
    vendor = VendorFactory.create()
    ShippingZoneFactory.create(vendor=vendor, code="home", countries=["FR"])
    printed = run("export", "--vendor", str(vendor.public_id))
    assert json.loads(printed) == export_config(vendor)
    csv_text = run("export", "--vendor", str(vendor.public_id), "--csv")
    assert csv_text.splitlines()[0] == ",".join(fmt.CSV_COLUMNS)


def test_a_csv_export_the_importer_could_not_read_fails_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vendor = VendorFactory.create()
    document = export_config(vendor)
    document["zones"] = [{"code": "home", "name": "x" * (fmt.MAX_CELL_CHARS + 1)}]
    monkeypatch.setattr(
        "apps.shipping.management.commands.shipping_config.export_config", lambda owner: document
    )
    with pytest.raises(CommandError, match="larger than"):
        run("export", "--vendor", str(vendor.public_id), "--csv")


def test_import_is_a_dry_run_unless_applied(tmp_path: Path) -> None:
    path, _ = vendor_file(tmp_path, "config.csv")
    target = VendorFactory.create()

    report = json.loads(run("import", str(path), "--vendor", str(target.public_id)))
    assert report["ok"] is True
    assert report["applied"] is False
    assert not ShippingZone.objects.filter(vendor=target).exists()

    report = json.loads(run("import", str(path), "--vendor", str(target.public_id), "--apply"))
    assert report["applied"] is True
    assert ShippingZone.objects.filter(vendor=target, code="home").exists()
    assert AuditLog.objects.get(action="shipping.config.imported").actor_label == (
        "command:shipping_config"
    )


def test_an_invalid_file_fails_the_command_with_its_report(tmp_path: Path) -> None:
    path, document = vendor_file(tmp_path)
    document["zones"][0]["countries"] = ["XX"]  # type: ignore[index]
    path.write_text(fmt.dump_json(document))
    target = VendorFactory.create()
    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "shipping_config",
            "import",
            str(path),
            "--vendor",
            str(target.public_id),
            "--apply",
            stdout=out,
        )
    rows = {row["ref"]: row for row in json.loads(out.getvalue())["rows"]}
    assert rows["zones[0]"]["errors"]["countries"]
    assert not ShippingZone.objects.filter(vendor=target).exists()


def test_staff_save_the_standard_template_and_a_vendor_copies_it(tmp_path: Path) -> None:
    path, document = vendor_file(tmp_path)
    run("save-template", str(path), "--code", "standard", "--name", "Standard", "--standard")
    template = ShippingTemplate.objects.get(code="standard")
    assert template.is_standard
    assert template.document == document

    vendor = VendorFactory.create()
    report = json.loads(run("copy-template", "--vendor", str(vendor.public_id), "--apply"))
    assert report["ok"] is True
    assert export_config(vendor) == document


def test_an_unknown_vendor_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="vendor"):
        run("export", "--vendor", "00000000-0000-0000-0000-000000000000")
