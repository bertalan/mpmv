"""I18N.19: CSV export of the sensitive catalog entries for the human review."""

import csv
import importlib.util
import io
import sys
from pathlib import Path

import polib
import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("i18n_catalog", ROOT / "tools" / "i18n_catalog.py")
assert _spec is not None
assert _spec.loader is not None
catalog = importlib.util.module_from_spec(_spec)
sys.modules["i18n_catalog"] = catalog
_spec.loader.exec_module(catalog)


@pytest.fixture
def fake_po(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    po = polib.POFile()
    po.append(
        polib.POEntry(
            msgid='Say "hi", friend',
            msgstr='Sagen Sie\n"hallo", Freund',
            occurrences=[("apps/orders/templates/email/orders/order_received/body.txt", "")],
        )
    )
    po.append(
        polib.POEntry(
            msgid="Item",
            msgid_plural="Items",
            msgctxt="cart",
            msgstr_plural={0: "Artikel", 1: "Artikel"},
            occurrences=[("themes/base/templates/components/cart_line.html", "")],
        )
    )
    po.append(
        polib.POEntry(
            msgid="Reviewed one",
            msgstr="Geprüft",
            tcomment="reviewed",
            occurrences=[("apps/compliance/forms.py", "")],
        )
    )
    po.append(
        polib.POEntry(
            msgid="Dashboard", msgstr="Übersicht", occurrences=[("apps/manager/views.py", "")]
        )
    )
    (tmp_path / "de" / "LC_MESSAGES").mkdir(parents=True)
    po.save(str(tmp_path / "de" / "LC_MESSAGES" / "django.po"))
    monkeypatch.setattr(catalog, "LOCALE", tmp_path)


def _export(**kw: bool) -> list[list[str]]:
    out = ROOT / "build" / "review-test.csv"
    out.parent.mkdir(exist_ok=True)
    catalog.review_export("de", out, **kw)
    with out.open(encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def test_columns_and_sensitive_filter(fake_po: None) -> None:
    rows = _export()
    assert tuple(rows[0]) == catalog.REVIEW_COLUMNS
    assert {r[0] for r in rows[1:]} == {'Say "hi", friend', "Item", "Reviewed one"}


def test_quoting_round_trips(fake_po: None) -> None:
    row = next(r for r in _export() if r[0].startswith("Say"))
    assert row[4] == 'Sagen Sie\n"hallo", Freund'
    assert row[3].endswith("order_received/body.txt")
    assert row[5] == "order emails and documents"


def test_plural_and_context(fake_po: None) -> None:
    row = next(r for r in _export() if r[0] == "Item")
    assert row[1] == "cart"
    assert row[2] == "Items"
    assert row[4] == "[0] Artikel | [1] Artikel"


def test_pending_skips_reviewed(fake_po: None) -> None:
    assert "Reviewed one" not in {r[0] for r in _export(pending=True)}


def test_all_includes_non_sensitive(fake_po: None) -> None:
    rows = _export(everything=True)
    assert "Dashboard" in {r[0] for r in rows}
    assert next(r for r in rows if r[0] == "Dashboard")[5] == "other"


def test_real_catalog_has_sensitive_entries_and_keeps_reviewed_mark() -> None:
    assert len(catalog.review_rows("de")) > 100
    e = polib.POEntry(msgid="x", tcomment="note\nreviewed")
    assert catalog.is_reviewed(e)
    assert not catalog.is_reviewed(polib.POEntry(msgid="x", tcomment="not reviewed yet"))


def test_stdout_export_has_header(fake_po: None, capsys: pytest.CaptureFixture[str]) -> None:
    catalog.review_export("de")
    first = next(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert tuple(first) == catalog.REVIEW_COLUMNS


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_a_cell_that_could_be_a_formula_is_neutralised(lead: str) -> None:
    assert catalog._safe_cell(f"{lead}SUM(A1)") == f"'{lead}SUM(A1)"


def test_an_ordinary_cell_is_left_alone() -> None:
    assert catalog._safe_cell("Dear customer") == "Dear customer"
    assert catalog._safe_cell("") == ""
