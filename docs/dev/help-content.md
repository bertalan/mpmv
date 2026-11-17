# Help content

The text of the "Help for this page" dialog (ARC.32) lives in the repository, not in the Wagtail
admin (ARC.36). It is the same for every operator. Agents write it in five languages; the
backfill of the per-screen files is ARC.37.

## Where the files are

`help/<lang>/<kind>/<stem>.md`

| Part | Values |
| --- | --- |
| `lang` | `en`, `it`, `de`, `fr`, `es` |
| `kind` | `route` (a screen), `page_type` (a CMS page class), `general` (the fallback) |
| `stem` | the key with `:` written as `__` |

Examples: `help/en/route/manager__shop__orders.md` (route `manager:shop:orders`),
`help/it/page_type/cms.BlogPostPage.md` (page type `cms.BlogPostPage`, case kept, as the lookup
uses `_meta.label`), `help/de/general/general.md` (the only name a general file may have).
A route name is the request's URL name, so a namespaced name such as `manager:shop:orders` is the
key. Never put `__` inside a real route name.

## Format

```
# Orders
last-checked: 2026-11-17
vendor-guide: yes

Plain Markdown. Short sentences for a non-technical reader.

## A section
- a list
```

- The first line is the title (up to 120 characters).
- `last-checked: YYYY-MM-DD` is required: the day someone compared the text with the screen.
  `vendor-guide: yes` is optional (links the vendor guide).
- The body is converted to rich text and passed through the CMS.12 sanitiser with the help
  features (headings become h4, bold, italic, lists, links). Anything else is dropped.
- Tone follows I18N.13: DE, FR and ES are formal for vendor and staff screens and informal for
  customer screens. DE, FR and ES are AI drafts until the translator's review (I18N.19).
  The general entry is the exception: every role sees it, vendors and staff included, so it is
  written in the formal register in all of DE (Sie), FR (vous), ES (usted) and IT (Lei).
- Name a button, field or menu item exactly as the screen shows it in that language: the msgstr
  of our catalog (`locale/`), else Wagtail's or Django's own. A label with no translation in any
  catalog shows in English on the screen (for example "Find coordinates", "Event review"), so the
  help quotes it in English too, and the missing msgid is a catalog follow-up.

## Placeholders

Operator-specific facts (names, e-mail addresses, fees, windows) are never typed in a file. Use a
placeholder: `{{ operator.support_email }}`. The allow-list is `PLACEHOLDERS` in
`apps/cms/help_files.py`: `operator.legal_name`, `operator.support_email`,
`operator.compliance_email`, `settings.site_name`. Values are read from `current_operator()` and
settings when the dialog is shown (never cached), HTML-escaped. An unknown placeholder fails
`sync_help` and the test suite. To add one, add it to the list in a reviewed PR.

## Sync

`manage.py sync_help [--dry-run]` creates or updates one `HelpEntry` per file, in the languages
that exist as Locales. It is idempotent and the files win: a staff row with the same language,
kind and key is replaced and marked `source="file"`. Rows made by file are read-only in the
Wagtail snippet admin for everyone, the superuser included (the permission policy refuses
change and delete on them, and the edit, delete and copy views answer 403); rows staff made for other keys stay editable. A row whose file was removed
is reported (`orphan`) and kept. `seed` calls it (a dry run stays a dry run), and the migrate
step of the container (`deploy/container/entrypoint.sh`, used by `mpmv-migrate`) runs it after
`migrate`; a failure there does not block a release (a bad file is caught by the test suite
first; stale help is cosmetic, not a safety risk) but logs one greppable line to stderr:
`ERROR sync_help failed: ...`. See the deploy runbook (`deploy/README.md`, "Help texts after a
deploy") when help looks stale after a release.

Decision (ARC.36): `sync_help` writes no `AuditLog`. The content comes from versioned files, so
git history is the audit trail (who, when, why, reviewed in a PR); the command prints its
`ChangeSet`. Staff edits of rows they made are not audited either.

## The coverage sweep and the baseline

`tests/integration/test_help_coverage.py` walks the signed-in routes (Manager, second factor,
checkout and account screens, a curated list of Wagtail and Django admin screens) and the CMS and
event page types. Each must have a file in every enabled customer language (`en`, `it`, until
the I18N.19 gate lifts), unless it is on the allow-list with a reason (redirect, POST-only, JSON,
modal fragment).

Today's debt is counted in `tests/help_coverage_baseline.txt` (first line `# count: N`, then
`route <name>` or `page_type <label>` per line). It only shrinks:

- a new route or page type without files fails the test;
- a baseline line that now has its files also fails: delete the line and lower the count.

Other checks: a file naming a route or page type that no longer exists fails; each entry has
files in all five languages with the same placeholders; the general entry exists. The test also
prints a warning (never a failure) when the route's view module or a template named in its view
changed in git after the file's `last-checked` date: re-read the screen, then update the date.

### Why most admin routes are not in the universe yet

`SPACE_NAMES` in the sweep lists nine Wagtail and Django admin screens. The rest (page chooser
variants, snippets of other models, users, groups, reports, workflows, the django-admin models)
is deferred to ARC.37 part 4: they are the frameworks' own screens, the admin help button is one
generic entry per screen family rather than per route name, and each needs a decision on what a
role may see there. ARC.37 part 4 owns them and widens `SPACE_NAMES`; until then the debt is
recorded here and in the sweep module's docstring.

### Enabled languages and the baseline gate

The sweep requires files in every enabled language, read from `settings.LANGUAGES` (the effective
set after the I18N.19 gate, via `MPMV_LANGUAGES`): when the gate lifts DE, FR and ES, the sweep
follows without an edit. The test suite runs with all five.

The baseline never grows: adding a key to `tests/help_coverage_baseline.txt` is not allowed and
reviewers reject it. The sweep fails on a line that has files or names a route that is gone,
and warns when a key is not on `origin/main`'s baseline (where git and that ref exist).

## Rule for every PR

A PR that adds a signed-in screen or a page type adds its help files in the five languages in the
same PR, or lists the key in the baseline only when the owner agrees (the baseline never grows).
