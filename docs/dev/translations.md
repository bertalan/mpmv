# Translation review files (I18N.13)

How a native speaker reviews the interface texts in a spreadsheet, and how the result goes back
into the catalogs. Source of truth: the gettext catalogs in `locale/<lang>/LC_MESSAGES/django.po`
(written by `tools/i18n_catalog.py`, compiled to `django.mo`). The export is a view of a catalog;
the import is the only way the file changes it.

## What goes in the file

| Scope | Texts |
|---|---|
| `emails` | texts used by email templates (`templates/email/`) and the notification and mail modules |
| `vendor-help` | texts used by the vendor-facing code (`apps/vendors/`, `apps/manager/`), help texts included |
| `all` (default) | every active entry of the catalog |

Scopes follow the source references (`#:` lines) of each entry. CMS pages are not in these files:
they are translated through wagtail-localize, which already has its own PO export and import
(Wagtail admin, Translations, per page and language: "Download PO file" and "Upload PO file").
Use that for pages; do not build a second mechanism.

A language with no catalog yet (for example `fr` or `es` before their I18N.7 slice) exports every
text of the first available catalog as `missing`; the import then creates the catalog.

## For the developer or the owner

```sh
uv run python manage.py i18n_export --lang de [--scope emails|vendor-help|all] --format xlsx|po
uv run python manage.py i18n_import build/i18n-review/i18n-de-all.xlsx --dry-run
uv run python manage.py i18n_import build/i18n-review/i18n-de-all.xlsx [--skip-invalid]
```

Files are written to `build/i18n-review/` (`--out` changes it) as `i18n-<lang>-<scope>.xlsx` or
`.po`. Send the file to the reviewer, wait for it to come back, run the import with `--dry-run`
first, then for real.

The import:

1. checks every row (see below) and prints a diff of the changes, row by row;
2. applies nothing when any row fails, unless `--skip-invalid`, which applies the valid rows and
   lists the skipped ones;
3. writes the `.po`, recompiles the `.mo` (the `fuzzy` flag of an edited entry is removed) and
   runs `tools/i18n_catalog.py check`;
4. writes one audit entry, `core.i18n.imported` (who, when, the SHA-256 of the file, counts of
   applied and skipped rows, never the texts; see [audit-trail.md](audit-trail.md));
5. with `--dry-run` does steps 1 and 2 only and writes nothing, not even an audit entry.

Commit the changed `.po` and `.mo` in a normal PR (task I18N.14 records the review per language).

## The XLSX file

Sheet 1 is **Read me**: the rules, in the reviewer's language when a translation of the Read me
exists (English, Italian, German, French and Spanish today), otherwise English. Sheet 2 is the language table: a frozen
banner with the short rules in row 1, the header in row 2, one row per text from row 3.

| Column | Content | Editable |
|---|---|---|
| msgid | stable key of the text | no |
| context | gettext context (for example "email subject") | no |
| form | plural form (0 or 1), empty for a text without plural | no |
| English source | the English text (for a plural, the singular or the plural form) | no |
| Current translation | what the catalog holds now | no |
| **Reviewer translation** | empty; the reviewer's proposal | **yes** |
| **Notes** | doubts and comments | **yes** |
| Flags | `fuzzy`, `missing` | no |
| Max length | guide value: twice the English length, at least 60 characters; a soft guide, never a refusal | no |
| Checksum (hidden) | SHA-256 of context, msgid, form, English source and current translation | no |

Sheets are protected without a password, with only Reviewer translation and Notes unlocked; the
protection stops accidents, the checksum is what the import trusts. An empty reviewer cell keeps
the current translation. Texts starting with `=` are stored as text, never as formulas.

## Rules checked on import

Each failing row is reported with its row number and the rule, in the words of the Read me:

| Rule | Refused when |
|---|---|
| placeholders | the set of `%(name)s`, `%s`, `%d` and `{name}` differs from the English source (order may change) |
| HTML tags | the tags or their attributes differ from the English source |
| new link | the text has an address (`http://`, `https://`, `www.`) that is not in the English source |
| checksum | msgid, context, form, English source, Current translation or Checksum was changed in the file, or the row names a text that is not in the catalog |
| stale | the catalog changed after the export (Current translation differs from the catalog) |

Length is a **warning**, not a rule: a text longer than Max length (German is often longer) is applied anyway and listed as `warning row N: ...` in the import output and in the report. Catalog entries do not know the field they fill, so no hard limit is enforced today; a real limit (a model field `max_length` mapped to the msgid) would be added as a refusal later.

A `.po` file is checked with the same rules (row = line of the entry in the file); the checksum
does not apply to it, the entry must exist in the catalog.

## Tone

Customer emails use the informal form of address: tu (Italian), du (German), tu (French), tú
(Spanish), owner decision. The Read me says so; the import cannot check it.

## Adding a Read me language

The Read me, the banner and the rule wording exist in EN, IT, DE, FR and ES (informal register: tu, du, tu, tú; the same content in each). To add another language, add it to `RULES`, `READ_ME` and `BANNERS` in `apps/core/i18n_review/engine.py`. The
rule texts are shown in the Read me and quoted in row errors, so one entry serves both.
