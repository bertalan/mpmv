"""The `i18n_export` and `i18n_import` commands (I18N.13): thin adapters over the services."""

from io import StringIO
from pathlib import Path

import polib
import pytest
from django.core.management import CommandError, call_command
from openpyxl import load_workbook

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def locale(tmp_path: Path) -> Path:
    po = polib.POFile()
    po.metadata = {"Language": "de", "Content-Type": "text/plain; charset=UTF-8"}
    po.append(polib.POEntry(msgid="Hello %(name)s", msgstr="Hallo %(name)s"))
    po.append(polib.POEntry(msgid="Contact us", msgstr="Kontakt"))
    path = tmp_path / "locale" / "de" / "LC_MESSAGES" / "django.po"
    path.parent.mkdir(parents=True)
    po.save(str(path))
    po.save_as_mofile(str(path.with_suffix(".mo")))
    return tmp_path / "locale"


def test_export_then_import_prints_the_diff(locale: Path, tmp_path: Path) -> None:
    out = StringIO()
    call_command(
        "i18n_export", "--lang", "de", "--format", "xlsx", "--out", str(tmp_path),
        "--locale-dir", str(locale), stdout=out,
    )  # fmt: skip
    path = tmp_path / "i18n-de-all.xlsx"
    assert path.exists()
    assert str(path) in out.getvalue()
    wb = load_workbook(path)
    for row in wb.worksheets[1].iter_rows(min_row=3):
        if row[0].value == "Contact us":
            row[5].value = "Kontaktieren Sie uns"
    wb.save(path)
    out = StringIO()
    call_command("i18n_import", str(path), "--locale-dir", str(locale), "--skip-check", stdout=out)
    assert "Contact us" in out.getvalue()
    assert "Kontaktieren Sie uns" in out.getvalue()
    assert AuditLog.objects.filter(action="core.i18n.imported").count() == 1


def test_import_with_an_error_fails_and_names_the_row(locale: Path, tmp_path: Path) -> None:
    call_command(
        "i18n_export", "--lang", "de", "--out", str(tmp_path), "--locale-dir", str(locale),
        stdout=StringIO(),
    )  # fmt: skip
    path = tmp_path / "i18n-de-all.xlsx"
    wb = load_workbook(path)
    for row in wb.worksheets[1].iter_rows(min_row=3):
        if row[0].value == "Hello %(name)s":
            row[5].value = "Hallo"
    wb.save(path)
    with pytest.raises(CommandError, match=r"Zeile [34]"):
        call_command(
            "i18n_import", str(path), "--locale-dir", str(locale), "--skip-check", stdout=StringIO()
        )
    assert not AuditLog.objects.filter(action="core.i18n.imported").exists()


def test_import_prints_a_length_warning_and_applies(locale: Path, tmp_path: Path) -> None:
    call_command(
        "i18n_export", "--lang", "de", "--out", str(tmp_path), "--locale-dir", str(locale),
        stdout=StringIO(),
    )  # fmt: skip
    path = tmp_path / "i18n-de-all.xlsx"
    wb = load_workbook(path)
    for row in wb.worksheets[1].iter_rows(min_row=3):
        if row[0].value == "Contact us":
            row[5].value = "k" * 500
    wb.save(path)
    out = StringIO()
    call_command("i18n_import", str(path), "--locale-dir", str(locale), "--skip-check", stdout=out)
    assert "warning" in out.getvalue().lower()


def test_a_bad_scope_or_format_is_refused(locale: Path, tmp_path: Path) -> None:
    with pytest.raises(CommandError):
        call_command("i18n_export", "--lang", "de", "--scope", "nope", "--out", str(tmp_path))
    with pytest.raises(CommandError):
        call_command("i18n_export", "--lang", "de", "--format", "csv", "--out", str(tmp_path))
