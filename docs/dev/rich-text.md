# Rich text (CMS.12)

Every rich text that reaches storage goes through one sanitiser: `apps.cms.engine.sanitise_rich_text(html, features)`.
It is the editor's own allow-list (Wagtail's `DbWhitelister` for the granted features), so only the elements and
attributes the editor can write are stored. It also reads the stored form (`<a linktype id>`, `<embed>`), keeps plain
text only (no CDATA, DOCTYPE, processing instructions or comments), adds `rel="noopener noreferrer"` to external links,
never raises, and is idempotent. `features=None` means Wagtail's default features.

## Where it runs

- A `pre_save` receiver (`apps/cms/signals.py`) calls `services.sanitise_rich_text_fields(instance)` for every model
  with a `RichTextField` or a `StreamField`, so `save()` from an import, a seed, the API or a migration is covered.
  With `save(update_fields=[...])` only the listed fields are cleaned, which are the only ones written.
- The Draftail converter reads the sanitised text, so the editor opens any stored rich text. `CmsConfig.ready` checks
  this at startup and refuses to start if a Wagtail upgrade breaks it.

## Rules for code in `apps/`

These writes skip `pre_save` and must not write a rich-text field:

- `bulk_create`, `bulk_update`
- `QuerySet.update(<rich-text field>=...)`
- `loaddata` (fixtures arrive with `raw=True`)

`tests/unit/test_richtext_write_paths.py` fails on `bulk_*` and on `update` of a rich-text field in non-test app code.
A batch writer that needs one of them calls `services.sanitise_rich_text_fields(instance)` on each instance first.

`Revision.content` (the JSON of a draft) is stored as saved; it is cleaned when the revision is published, because
publishing calls `save()`.
