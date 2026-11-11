"""Export and re-import of translatable texts for native-speaker review (I18N.13)."""

import hashlib
from pathlib import Path

import polib
import pytest
from openpyxl import Workbook, load_workbook

from apps.core.audit import AuditContext
from apps.core.i18n_review import engine
from apps.core.i18n_review.engine import Rule
from apps.core.i18n_review.services import ImportReport, export_catalog, import_file
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="command:i18n_import", source=AuditLog.Source.COMMAND)

# msgid, msgctxt, occurrence, German translation
ROWS = [
    ("Hello %(name)s", None, "apps/orders/templates/email/orders/hi/body.txt", "Hallo %(name)s"),
    ("Open <b>the shop</b>", None, "apps/vendors/forms.py", "Shop <b>öffnen</b>"),
    ("Order paid", "email subject", "apps/orders/templates/email/orders/paid/subject.txt", ""),
    ("Contact us", None, "apps/manager/views.py", "Kontakt"),
]


def _po(locale: Path, lang: str, rows: list[tuple[str, str | None, str, str]]) -> Path:
    po = polib.POFile()
    po.metadata = {
        "Language": lang,
        "Content-Type": "text/plain; charset=UTF-8",
        "Plural-Forms": "nplurals=2; plural=(n != 1);",
    }
    for msgid, ctx, place, msgstr in rows:
        po.append(polib.POEntry(msgid=msgid, msgctxt=ctx, msgstr=msgstr, occurrences=[(place, "")]))
    path = locale / lang / "LC_MESSAGES" / "django.po"
    path.parent.mkdir(parents=True)
    po.save(str(path))
    po.save_as_mofile(str(path.with_suffix(".mo")))
    return path


@pytest.fixture
def locale(tmp_path: Path) -> Path:
    loc = tmp_path / "locale"
    _po(loc, "de", ROWS)
    return loc


def _export(locale: Path, tmp_path: Path, **kw: str) -> Path:
    return export_catalog(
        lang="de", scope=kw.get("scope", "all"), fmt="xlsx", out_dir=tmp_path, locale_dir=locale
    )


def _edit(path: Path, edits: dict[str, str]) -> None:
    """Set the reviewer column on the row whose msgid is the key."""
    wb = load_workbook(path)
    ws = wb.worksheets[1]
    for row in ws.iter_rows(min_row=3):
        if row[0].value in edits:
            row[5].value = edits[row[0].value]
    wb.save(path)


def _row_of(path: Path, msgid: str) -> int:
    ws = load_workbook(path).worksheets[1]
    return int(next(r[0].row for r in ws.iter_rows(min_row=3) if r[0].value == msgid))


def _catalog(locale: Path) -> dict[str, str]:
    po = polib.pofile(str(locale / "de" / "LC_MESSAGES" / "django.po"))
    return {e.msgid: e.msgstr for e in po}


def _import(locale: Path, path: Path, **kw: bool) -> ImportReport:
    return import_file(path, locale_dir=locale, ctx=CTX, **kw)


# --- layout -----------------------------------------------------------------------------------


def test_read_me_is_the_first_sheet_and_the_banner_is_frozen(locale: Path, tmp_path: Path) -> None:
    wb = load_workbook(_export(locale, tmp_path))
    assert wb.sheetnames[0] == "Read me"
    ws = wb.worksheets[1]
    assert ws.freeze_panes == "A3"
    assert ws["A1"].value == engine.banner("en", "de")
    header = [c.value for c in ws[2]]
    assert header == [
        "msgid",
        "context",
        "form",
        "English source",
        "Current translation",
        "Reviewer translation",
        "Notes",
        "Flags",
        "Max length",
        "Checksum",
    ]


def test_read_me_lists_every_rule_in_english_for_german(locale: Path, tmp_path: Path) -> None:
    wb = load_workbook(_export(locale, tmp_path))
    text = [r[0].value for r in wb["Read me"].iter_rows() if r[0].value]
    for rule in Rule:
        assert engine.rule_text(rule, "en") in text


def test_read_me_in_italian_when_the_reviewer_language_has_one(tmp_path: Path) -> None:
    loc = tmp_path / "loc-it"
    _po(loc, "it", ROWS)
    path = export_catalog(
        lang="it", scope="all", fmt="xlsx", out_dir=tmp_path, locale_dir=loc, readme_lang=None
    )
    text = [r[0].value for r in load_workbook(path)["Read me"].iter_rows() if r[0].value]
    assert engine.rule_text(Rule.PLACEHOLDERS, "it") in text


def test_only_the_reviewer_and_notes_columns_are_unlocked(locale: Path, tmp_path: Path) -> None:
    ws = load_workbook(_export(locale, tmp_path)).worksheets[1]
    assert ws.protection.sheet is True
    assert not ws.protection.password
    row = ws[3]
    unlocked = [c.column_letter for c in row if not c.protection.locked]
    assert unlocked == ["F", "G"]
    assert ws.column_dimensions["J"].hidden is True


def test_flags_and_max_length_columns(locale: Path, tmp_path: Path) -> None:
    ws = load_workbook(_export(locale, tmp_path)).worksheets[1]
    by_id = {r[0].value: r for r in ws.iter_rows(min_row=3)}
    assert by_id["Order paid"][7].value == "missing"
    assert by_id["Order paid"][1].value == "email subject"
    assert by_id["Contact us"][7].value in (None, "")
    assert by_id["Contact us"][8].value == engine.max_length("Contact us")


def test_scopes_filter_by_where_the_text_is_used(locale: Path, tmp_path: Path) -> None:
    def ids(scope: str) -> set[str]:
        path = export_catalog(
            lang="de", scope=scope, fmt="xlsx", out_dir=tmp_path, locale_dir=locale
        )
        return {r[0].value for r in load_workbook(path).worksheets[1].iter_rows(min_row=3)}

    assert ids("emails") == {"Hello %(name)s", "Order paid"}
    assert ids("vendor-help") == {"Open <b>the shop</b>", "Contact us"}
    assert len(ids("all")) == 4


def test_po_format_for_poedit(locale: Path, tmp_path: Path) -> None:
    path = export_catalog(lang="de", scope="emails", fmt="po", out_dir=tmp_path, locale_dir=locale)
    assert path.suffix == ".po"
    assert {e.msgid for e in polib.pofile(str(path))} == {"Hello %(name)s", "Order paid"}


def test_a_language_without_a_catalog_exports_every_text_as_missing(tmp_path: Path) -> None:
    loc = tmp_path / "loc"
    _po(loc, "it", ROWS)
    path = export_catalog(lang="fr", scope="all", fmt="xlsx", out_dir=tmp_path, locale_dir=loc)
    ws = load_workbook(path).worksheets[1]
    assert {r[7].value for r in ws.iter_rows(min_row=3)} == {"missing"}


# --- round trip -------------------------------------------------------------------------------


def test_round_trip_changes_exactly_the_edited_strings(locale: Path, tmp_path: Path) -> None:
    before = _catalog(locale)
    path = _export(locale, tmp_path)
    _edit(path, {"Contact us": "Kontaktieren Sie uns", "Order paid": "Bestellung bezahlt"})
    report = _import(locale, path)
    after = _catalog(locale)
    assert not report.errors
    assert {k for k in after if after[k] != before[k]} == {"Contact us", "Order paid"}
    assert after["Contact us"] == "Kontaktieren Sie uns"
    assert after["Order paid"] == "Bestellung bezahlt"
    assert len(report.applied) == 2
    # the .mo is recompiled
    mo = polib.mofile(str(locale / "de" / "LC_MESSAGES" / "django.mo"))
    assert mo.find("Contact us").msgstr == "Kontaktieren Sie uns"


def test_an_untouched_file_changes_nothing(locale: Path, tmp_path: Path) -> None:
    before = _catalog(locale)
    report = _import(locale, _export(locale, tmp_path))
    assert report.applied == []
    assert report.errors == []
    assert _catalog(locale) == before


def test_a_reviewer_clears_nothing_by_leaving_the_cell_empty(locale: Path, tmp_path: Path) -> None:
    path = _export(locale, tmp_path)
    report = _import(locale, path)
    assert report.applied == []
    assert _catalog(locale)["Contact us"] == "Kontakt"


def test_a_fuzzy_entry_loses_its_flag_when_edited(locale: Path, tmp_path: Path) -> None:
    pofile = locale / "de" / "LC_MESSAGES" / "django.po"
    po = polib.pofile(str(pofile))
    po.find("Contact us").flags.append("fuzzy")
    po.save()
    path = _export(locale, tmp_path)
    _edit(path, {"Contact us": "Kontaktieren Sie uns"})
    _import(locale, path)
    assert "fuzzy" not in polib.pofile(str(pofile)).find("Contact us").flags


# --- refusals ---------------------------------------------------------------------------------

REFUSALS = [
    ("Hello %(name)s", "Hallo %(nome)s", Rule.PLACEHOLDERS),
    ("Open <b>the shop</b>", "Shop <i>öffnen</i>", Rule.HTML_TAGS),
    ("Contact us", "Kontakt https://evil.example/", Rule.NEW_URL),
    ("Contact us", "k" * 500, Rule.LENGTH),
]


@pytest.mark.parametrize(("msgid", "text", "rule"), REFUSALS)
def test_each_rule_is_refused_with_the_row_number_and_the_rule_text(
    locale: Path, tmp_path: Path, msgid: str, text: str, rule: Rule
) -> None:
    before = _catalog(locale)
    path = _export(locale, tmp_path)
    _edit(path, {msgid: text, "Order paid": "Bestellung bezahlt"})
    report = _import(locale, path)
    row = _row_of(path, msgid)
    assert [(e.row, e.rule) for e in report.errors] == [(row, rule)]
    assert engine.rule_text(rule, "en") in report.errors[0].message
    assert f"{row}" in report.errors[0].message
    # nothing applied on any error, not even the valid row
    assert _catalog(locale) == before
    assert report.applied == []


def test_skip_invalid_applies_the_valid_rows_and_reports_the_rest(
    locale: Path, tmp_path: Path
) -> None:
    path = _export(locale, tmp_path)
    _edit(path, {"Hello %(name)s": "Hallo %(nome)s", "Contact us": "Kontaktieren Sie uns"})
    report = _import(locale, path, skip_invalid=True)
    assert len(report.errors) == 1
    assert report.skipped == 1
    assert _catalog(locale)["Contact us"] == "Kontaktieren Sie uns"
    assert _catalog(locale)["Hello %(name)s"] == "Hallo %(name)s"


def test_a_changed_msgid_or_checksum_is_refused(locale: Path, tmp_path: Path) -> None:
    path = _export(locale, tmp_path)
    wb = load_workbook(path)
    ws = wb.worksheets[1]
    ws.cell(row=3, column=1).value = "Tampered"  # msgid
    ws.cell(row=4, column=6).value = "Anders"
    ws.cell(row=4, column=10).value = "0" * 64  # checksum
    wb.save(path)
    report = _import(locale, path)
    assert {(e.row, e.rule) for e in report.errors} == {(3, Rule.CHECKSUM), (4, Rule.CHECKSUM)}


def test_a_catalog_changed_since_the_export_is_refused(locale: Path, tmp_path: Path) -> None:
    path = _export(locale, tmp_path)
    pofile = locale / "de" / "LC_MESSAGES" / "django.po"
    po = polib.pofile(str(pofile))
    po.find("Contact us").msgstr = "Neu"
    po.save()
    _edit(path, {"Contact us": "Kontaktieren Sie uns"})
    report = _import(locale, path)
    assert [e.rule for e in report.errors] == [Rule.STALE]
    assert _catalog(locale)["Contact us"] == "Neu"


def test_a_file_that_is_not_an_export_is_refused(locale: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "other.xlsx"
    Workbook().save(bogus)
    with pytest.raises(ValueError, match="not an export"):
        _import(locale, bogus)


# --- dry run, audit ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_audits_nothing(locale: Path, tmp_path: Path) -> None:
    path = _export(locale, tmp_path)
    _edit(path, {"Contact us": "Kontaktieren Sie uns"})
    po_before = (locale / "de" / "LC_MESSAGES" / "django.po").read_bytes()
    mo_before = (locale / "de" / "LC_MESSAGES" / "django.mo").read_bytes()
    report = _import(locale, path, dry_run=True)
    assert len(report.applied) == 1
    assert (locale / "de" / "LC_MESSAGES" / "django.po").read_bytes() == po_before
    assert (locale / "de" / "LC_MESSAGES" / "django.mo").read_bytes() == mo_before
    assert not AuditLog.objects.filter(action="core.i18n.imported").exists()


def test_an_import_is_audited_with_the_file_hash(locale: Path, tmp_path: Path) -> None:
    path = _export(locale, tmp_path)
    _edit(path, {"Contact us": "Kontaktieren Sie uns"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _import(locale, path)
    entry = AuditLog.objects.get(action="core.i18n.imported")
    assert entry.after is not None
    assert entry.actor_label == "command:i18n_import"
    assert entry.after["sha256"] == digest
    assert entry.after["applied"] == 1
    assert entry.after["skipped"] == 0
    assert entry.after["lang"] == "de"
    assert "Kontaktieren" not in str(entry.after)


def test_po_round_trip_with_the_same_validation(locale: Path, tmp_path: Path) -> None:
    path = export_catalog(lang="de", scope="all", fmt="po", out_dir=tmp_path, locale_dir=locale)
    po = polib.pofile(str(path))
    po.find("Contact us").msgstr = "Kontaktieren Sie uns"
    po.find("Hello %(name)s").msgstr = "Hallo"
    po.save()
    report = _import(locale, path, skip_invalid=True)
    assert [e.rule for e in report.errors] == [Rule.PLACEHOLDERS]
    assert _catalog(locale)["Contact us"] == "Kontaktieren Sie uns"
