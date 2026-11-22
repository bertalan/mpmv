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
| placeholders | the multiset of placeholders differs from the English source: printf forms with flags, width and precision (`%(name)s`, `%.2f`, `%5s`, `%%`), `str.format` fields parsed with `string.Formatter` and compared as (name, conversion, format spec), nested fields in a spec included (`{0}`, `{0:.2f}`, `{name!r}`, `{0!s:>{1}}`); unbalanced braces are refused; printf flags include the space flag (`% d`), and Django template syntax (`{{ }}`, `{% %}`, `{# #}`). Order may change. A field with attribute or index access (`{a.__class__}`, `{a[0]}`) is refused unless the English source has the same one |
| HTML tags | read with a real HTML parser (`html.parser`): the tags (unclosed or broken ones included) and their attribute names and values differ from the source, or an event-handler attribute (`on...`) appears |
| new link | the text has a link-like token that is not in the English source: `http:`, `https:`, `ftp:`, `mailto:`, `javascript:`, `vbscript:`, `data:`, `file:`, `tel:`, `sms:`, `//host`, `www.` (any case, also inside attributes, and also when hidden by HTML entities or control characters) |
| formula | an XLSX cell in Reviewer translation or Notes is a formula (starts with `=`) |
| duplicate | the same msgid, context and form appears twice in the file (every copy is refused) |
| checksum | msgid, context, form, English source, Current translation or Checksum was changed in the file, or the row names a text that is not in the catalog |
| stale | the catalog changed after the export (XLSX: Current translation differs from the catalog; `.po`: the export marker in the entry's translator comment, `mpmv-base:`, no longer matches the catalog, or is missing) |

File-level limits, refused with a plain message before anything is read: the language must be one
of `settings.LANGUAGES` (exact code; it names the catalog folder, so a file naming any other
language is refused), the file is at most 5 MB (50 MB unpacked, counted on the bytes really decompressed, not the sizes the zip declares, which stops zip bombs) and at most
20,000 rows, and a corrupt or non-XLSX file gives "is not a valid .xlsx file".

The `.po` and `.mo` are written through temporary files in the same folder, fsynced, then
`os.replace`d (the mode of the replaced file is kept, 0644 for a new one); if the second replace fails the first is rolled back, so the catalog and its `.mo`
never end up out of step.

Length is a **warning**, not a rule: a text longer than Max length (German is often longer) is applied anyway and listed as `warning row N: ...` in the import output and in the report. Catalog entries do not know the field they fill, so no hard limit is enforced today; a real limit (a model field `max_length` mapped to the msgid) would be added as a refusal later.

A `.po` file is checked with the same rules (row = line of the entry in the file); it has no
checksum column, so the stale check above uses the `mpmv-base:` marker that the export writes in each
entry (keep Poedit's translator comments).

## Tone

Customer emails use the informal form of address: tu (Italian), du (German), tu (French), tú
(Spanish), owner decision. The Read me says so; the import cannot check it. The second-factor
screens (`apps/accounts/templates/accounts/mfa/`) are shared by customers, vendors and staff and
use the formal register (Lei, Sie, vous, usted). A msgid shared by a customer page and a formal page
cannot have two registers: the customer page gets its own gettext context (for example
"Your data" with context "customer privacy page"). Catalog tests check both registers. On the
Manager and the vendor mails, informal pronouns are refused anywhere and an informal imperative
("Annulla", "Scegli", "Wähle", "Choisis", "Elige") is refused at the start of a sentence; a
button or link label (up to twelve words, no sentence punctuation) may stay an imperative. The
Italian help uses the ASCII apostrophe only.

## Adding a Read me language

The Read me, the banner and the rule wording exist in EN, IT, DE, FR and ES (informal register: tu, du, tu, tú; the same content in each). To add another language, add it to `RULES`, `READ_ME` and `BANNERS` in `apps/core/i18n_review/engine.py`. The
rule texts are shown in the Read me and quoted in row errors, so one entry serves both.

## German, French and Spanish: the review gate (I18N.19)

The `de`, `fr` and `es` catalogs are AI drafts (#588). Until a professional translator has reviewed
the sensitive strings and counsel has signed off the legal and tax wording, production serves only
`en,it`: the default of `MPMV_LANGUAGES` leaves the three languages out, and the system check
`core.E915` refuses a LIVE deploy that lists a language other than `en` and `it` whose catalog has
no review mark (`core.W915`, a warning, elsewhere). Staff preview them on STAGE with
`MPMV_LANGUAGES=en,it,de,fr,es`.

```sh
python tools/i18n_catalog.py review-export <lang> [--all] [--pending] [--out FILE]
```

writes the entries in `SENSITIVE_GROUPS` (order emails and documents, compliance forms, DSA statements of reasons and event moderation emails (EVT.11), terms,
price notes, cart and order components, order-change pages) as a CSV for the translator.
A reviewed entry carries the translator comment `# reviewed` in the `.po`.

```sh
python tools/i18n_catalog.py mark-reviewed <lang>
```

is the translator's act, after the review: it adds `# reviewed` to every sensitive entry and the
header line `# review: sensitive entries reviewed (I18N.19)` that the check reads (a text edit,
nothing is re-wrapped). Never run it on an unreviewed catalog. The staff preview switch and the
counsel sign-off mark are not built yet (I18N.19).

## Absolute URLs (I18N.18)

`MPMV_BASE_URL` is the public https root. On LIVE and STAGE the check `core.E921` fails when it is
empty, a placeholder or not https, or when the default Wagtail `Site` differs from it (the seed
syncs the `Site`). `site_root_url()` raises `ImproperlyConfigured` in production settings and never
returns an empty string, so a canonical, hreflang or sitemap URL is never relative.

## Vendor self-certification texts (CMP.25)

The DSA article 30(1)(e) statement a vendor owner accepts is a Python literal per language in
`SELF_CERTIFICATION_TEXTS` (`apps/vendors/engine.py`), not a gettext entry: the acceptance stores
the version and the hash of the exact text. `review-export` does not export it, so hand it to the
translator separately. The review gate covers it with its own mark:

- `vendors.E001` (`vendors.W001` outside LIVE): a configured language has no text.
- `vendors.E002` (`vendors.W002` outside LIVE): a configured language has a text but is not in
  `SELF_CERTIFICATION_REVIEWED_LANGUAGES`. Add the language there in the commit that carries the
  text the translator approved. `en` and `it` are served without the gate.

Changing a text after an acceptance of it may exist needs a bump of `SELF_CERTIFICATION_VERSION`;
a unit test pins the digests of each version and fails otherwise.
