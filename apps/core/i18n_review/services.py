"""Export and re-import of translatable texts for native-speaker review (I18N.13).

The gettext `.po` catalogs stay the source of truth (`locale/<lang>/LC_MESSAGES/django.po`,
written by `tools/i18n_catalog.py`). `export_catalog` writes a reviewer file (XLSX, or the `.po`
itself for Poedit users); `import_file` validates the reviewer's edits against the rules of
`engine`, writes the `.po` and recompiles the `.mo`. CMS pages are not handled here: they have
their own PO export and import in wagtail-localize (docs/dev/translations.md).
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polib
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, Protection
from openpyxl.worksheet.worksheet import Worksheet

from apps.core.audit import AuditContext, record

from . import engine
from .engine import Rule

IMPORT_ACTION = "core.i18n.imported"
SCOPES = ("emails", "vendor-help", "all")
READ_ME_SHEET = "Read me"
MARKER = "mpmv-i18n:1"
HEADER = [
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
FIRST_ROW = 3  # row 1 is the banner, row 2 the header
(
    C_MSGID,
    C_CTX,
    C_FORM,
    C_ENGLISH,
    C_CURRENT,
    C_REVIEWER,
    C_NOTES,
    C_FLAGS,
    C_MAX,
    C_SUM,
) = range(10)


@dataclass(frozen=True)
class Change:
    row: int
    msgid: str
    context: str
    form: int | None
    old: str
    new: str


@dataclass(frozen=True)
class RowError:
    row: int
    rule: Rule
    message: str


@dataclass
class ImportReport:
    lang: str
    sha256: str
    applied: list[Change] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    warnings: list[RowError] = field(default_factory=list)
    skipped: int = 0


# --- catalogs ---------------------------------------------------------------------------------


def catalog_path(locale_dir: Path, lang: str) -> Path:
    return locale_dir / lang / "LC_MESSAGES" / "django.po"


def load_catalog(locale_dir: Path, lang: str) -> polib.POFile:
    """The catalog of `lang`; a language with none yet starts from every text of another one,
    untranslated."""
    path = catalog_path(locale_dir, lang)
    if path.exists():
        return polib.pofile(str(path))
    others = sorted(p for p in locale_dir.glob("*/LC_MESSAGES/django.po") if p.parts[-3] != lang)
    po = polib.POFile(wrapwidth=100)
    po.metadata = {
        "Project-Id-Version": "MPMV",
        "Language": lang,
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": "nplurals=2; plural=(n != 1);",
    }
    if others:
        for e in polib.pofile(str(others[0])):
            po.append(
                polib.POEntry(
                    msgid=e.msgid,
                    msgctxt=e.msgctxt,
                    msgid_plural=e.msgid_plural,
                    msgstr_plural={0: "", 1: ""} if e.msgid_plural else {},
                    occurrences=e.occurrences,
                )
            )
    return po


def in_scope(entry: polib.POEntry, scope: str) -> bool:
    if scope == "all":
        return True
    places = [p for p, _ in entry.occurrences]
    if scope == "emails":
        return any("/email/" in p or p.endswith(("/notifications.py", "/mail.py")) for p in places)
    return any(p.startswith(("apps/vendors/", "apps/manager/")) for p in places)


def _forms(entry: polib.POEntry) -> list[tuple[int | None, str, str]]:
    """(form, English source, current translation) for each translatable form of an entry."""
    if entry.msgid_plural:
        return [
            (0, entry.msgid, entry.msgstr_plural.get(0, "")),
            (1, entry.msgid_plural, entry.msgstr_plural.get(1, "")),
        ]
    return [(None, entry.msgid, entry.msgstr)]


def _cell(ws: Worksheet, row: int, col: int, value: Any) -> Any:
    cell = ws.cell(row=row, column=col + 1)
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"  # never a formula
    return cell


# --- export -----------------------------------------------------------------------------------


def export_catalog(
    *,
    lang: str,
    scope: str,
    fmt: str,
    out_dir: Path,
    locale_dir: Path,
    readme_lang: str | None = None,
) -> Path:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    po = load_catalog(locale_dir, lang)
    entries = [e for e in po if not e.obsolete and in_scope(e, scope)]
    out_dir.mkdir(parents=True, exist_ok=True)
    reader = readme_lang or engine.read_me_language(lang)
    if fmt == "po":
        return _export_po(po, entries, lang, scope, out_dir)
    if fmt != "xlsx":
        raise ValueError(f"unknown format {fmt!r}")
    return _export_xlsx(entries, lang, scope, out_dir, reader)


def _export_po(
    po: polib.POFile, entries: list[polib.POEntry], lang: str, scope: str, out_dir: Path
) -> Path:
    out = polib.POFile(wrapwidth=100)
    out.metadata = dict(po.metadata)
    out.metadata["Language"] = lang
    out.header = "Review file: edit msgstr only; keep placeholders and HTML tags; no new links."
    for e in entries:
        out.append(
            polib.POEntry(
                msgid=e.msgid,
                msgctxt=e.msgctxt,
                msgid_plural=e.msgid_plural,
                msgstr=e.msgstr,
                msgstr_plural=dict(e.msgstr_plural),
                flags=list(e.flags),
                occurrences=e.occurrences,
                comment=f"max length: {engine.max_length(e.msgid)}",
            )
        )
    path = out_dir / f"i18n-{lang}-{scope}.po"
    out.save(str(path))
    return path


def _export_xlsx(
    entries: list[polib.POEntry], lang: str, scope: str, out_dir: Path, reader: str
) -> Path:
    wb = Workbook()
    wb.properties.description = f"{MARKER} lang={lang} readme={reader}"
    readme = wb.worksheets[0]
    readme.title = READ_ME_SHEET
    readme.column_dimensions["A"].width = 120
    for i, line in enumerate(engine.read_me_lines(reader, lang), start=1):
        cell = _cell(readme, i, 0, line)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if i == 1:
            cell.font = Font(bold=True, size=14)
    readme.protection.sheet = True

    ws = wb.create_sheet(lang.upper())
    banner = _cell(ws, 1, 0, engine.banner(reader, lang))
    banner.font = Font(bold=True)
    banner.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADER) - 1)
    ws.row_dimensions[1].height = 48
    for col, name in enumerate(HEADER):
        _cell(ws, 2, col, name).font = Font(bold=True)
    row = FIRST_ROW
    for e in entries:
        for form, english, current in _forms(e):
            limit = engine.max_length(e.msgid)
            ctx = e.msgctxt or ""
            flags = []
            if "fuzzy" in e.flags:
                flags.append("fuzzy")
            if not current:
                flags.append("missing")
            values = {
                C_MSGID: e.msgid,
                C_CTX: e.msgctxt or None,
                C_FORM: form,
                C_ENGLISH: english,
                C_CURRENT: current or None,
                C_REVIEWER: None,
                C_NOTES: None,
                C_FLAGS: ", ".join(flags) or None,
                C_MAX: limit,
                C_SUM: engine.checksum(ctx, e.msgid, form, english, current),
            }
            for col, value in values.items():
                cell = _cell(ws, row, col, value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.protection = Protection(locked=col not in (C_REVIEWER, C_NOTES))
            row += 1
    widths = [30, 14, 6, 45, 45, 45, 30, 10, 10, 66]
    for col, width in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + col)].width = width
    ws.column_dimensions["J"].hidden = True
    ws.freeze_panes = f"A{FIRST_ROW}"
    ws.protection.sheet = True
    path = out_dir / f"i18n-{lang}-{scope}.xlsx"
    wb.save(path)
    return path


# --- import -----------------------------------------------------------------------------------


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _form(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def import_file(
    path: Path,
    *,
    locale_dir: Path,
    ctx: AuditContext,
    dry_run: bool = False,
    skip_invalid: bool = False,
) -> ImportReport:
    """Validate the reviewer's edits and apply them to the catalog.

    Nothing is written when a row fails, unless `skip_invalid` (the failing rows are skipped and
    reported). `dry_run` validates and reports without writing or auditing.
    """
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix == ".po":
        lang, reader, edits = _read_po(path, locale_dir)
    else:
        lang, reader, edits = _read_xlsx(path)
    po = load_catalog(locale_dir, lang)
    report = ImportReport(lang=lang, sha256=sha)
    pending: list[tuple[polib.POEntry, Change]] = []
    for edit in edits:
        if edit.error is not None:
            report.errors.append(
                RowError(edit.row, edit.error, engine.row_message(edit.row, edit.error, reader))
            )
            continue
        found = _lookup(po, edit)
        if found is None:
            report.errors.append(
                RowError(
                    edit.row, Rule.CHECKSUM, engine.row_message(edit.row, Rule.CHECKSUM, reader)
                )
            )
            continue
        entry, english, current = found
        if edit.stale_check and current != edit.current:
            report.errors.append(
                RowError(edit.row, Rule.STALE, engine.row_message(edit.row, Rule.STALE, reader))
            )
            continue
        if edit.reviewer == current:
            continue
        broken = engine.validate(english, current, edit.reviewer, engine.max_length(edit.msgid))
        if engine.over_guide(edit.reviewer, engine.max_length(edit.msgid)):
            message = engine.row_message(edit.row, Rule.LENGTH, reader)
            report.warnings.append(RowError(edit.row, Rule.LENGTH, message))
        for rule in broken:
            message = engine.row_message(edit.row, rule, reader)
            report.errors.append(RowError(edit.row, rule, message))
        if not broken:
            pending.append(
                (entry, Change(edit.row, edit.msgid, edit.ctx, edit.form, current, edit.reviewer))
            )
    bad_rows = {e.row for e in report.errors}
    report.skipped = len(bad_rows) if skip_invalid else 0
    if bad_rows and not skip_invalid:
        return report
    report.applied = [change for _, change in pending]
    if dry_run or not pending:
        return report
    for entry, change in pending:
        if change.form is None:
            entry.msgstr = change.new
        else:
            entry.msgstr_plural[change.form] = change.new
        if "fuzzy" in entry.flags:
            entry.flags.remove("fuzzy")
    target = catalog_path(locale_dir, lang)
    target.parent.mkdir(parents=True, exist_ok=True)
    po.save(str(target))
    po.save_as_mofile(str(target.with_suffix(".mo")))
    record(
        ctx,
        IMPORT_ACTION,
        "core.i18n_catalog",
        lang,
        None,
        {
            "lang": lang,
            "file": path.name,
            "sha256": sha,
            "applied": len(report.applied),
            "skipped": report.skipped,
            "warnings": len(report.warnings),
        },
    )
    return report


@dataclass(frozen=True)
class _Edit:
    row: int
    msgid: str
    ctx: str
    form: int | None
    current: str
    reviewer: str
    stale_check: bool = True
    error: Rule | None = None


def _lookup(po: polib.POFile, edit: _Edit) -> tuple[polib.POEntry, str, str] | None:
    entry = po.find(edit.msgid, msgctxt=edit.ctx or None)
    if entry is None or entry.obsolete:
        return None
    for form, english, current in _forms(entry):
        if form == edit.form:
            return entry, english, current
    return None


def _read_xlsx(path: Path) -> tuple[str, str, list[_Edit]]:
    wb = load_workbook(path)
    description = wb.properties.description or ""
    if not description.startswith(MARKER) or len(wb.worksheets) < 2:
        raise ValueError(f"{path.name} is not an export of i18n_export")
    meta = dict(p.split("=", 1) for p in description.split()[1:] if "=" in p)
    lang, reader = meta.get("lang", ""), meta.get("readme", "en")
    ws = wb.worksheets[1]
    if [c.value for c in ws[2]][: len(HEADER)] != HEADER:
        raise ValueError(f"{path.name} is not an export of i18n_export: header changed")
    edits: list[_Edit] = []
    for cells in ws.iter_rows(min_row=FIRST_ROW):
        row = cells[0].row
        v = [c.value for c in cells] + [None] * len(HEADER)
        if all(x in (None, "") for x in v[: len(HEADER)]):
            continue
        msgid, ctx, english, current = (
            _text(v[C_MSGID]),
            _text(v[C_CTX]),
            _text(v[C_ENGLISH]),
            _text(v[C_CURRENT]),
        )
        try:
            form = _form(v[C_FORM])
        except (TypeError, ValueError):
            form = -1
        expected = engine.checksum(ctx, msgid, form, english, current)
        reviewer = _text(v[C_REVIEWER]).strip()
        error = None if _text(v[C_SUM]) == expected else Rule.CHECKSUM
        if error is None and not reviewer:
            continue
        edits.append(_Edit(row, msgid, ctx, form, current, reviewer, error=error))
    return lang, reader, edits


def _read_po(path: Path, locale_dir: Path) -> tuple[str, str, list[_Edit]]:
    po = polib.pofile(str(path))
    lang = po.metadata.get("Language", "")
    reader = engine.read_me_language(lang)
    catalog = load_catalog(locale_dir, lang)
    edits: list[_Edit] = []
    for e in po:
        row = e.linenum
        known = catalog.find(e.msgid, msgctxt=e.msgctxt or None)
        if known is None:
            edits.append(_Edit(row, e.msgid, e.msgctxt or "", None, "", "", error=Rule.CHECKSUM))
            continue
        theirs = {f: text for f, _, text in _forms(e)}
        for form, _english, current in _forms(known):
            text = theirs.get(form, "").strip()
            if text and text != current:
                edits.append(
                    _Edit(row, e.msgid, e.msgctxt or "", form, current, text, stale_check=False)
                )
    return lang, reader, edits
