# Field help texts

Every field that an editor, a vendor or a staff member fills in has help text. This page says how to
write it and how the sweep test (ARC.30) enforces it. The rule itself is the "Field help" row in the
cross-cutting conventions of [spec 01](../spec/01-stack-and-architecture.md#cross-cutting-conventions).

## Where help text goes

| Surface | Where to write it |
| --- | --- |
| Wagtail page and snippet fields | `help_text=` on the model field (or on the `FieldPanel`) |
| StreamField blocks | `help_text=` on each block inside a `StructBlock`, `StreamBlock` or `ListBlock` |
| Manager forms and other plain forms | `help_text=` on the form field |
| Django admin | `help_text=` on the model field, or on the field of the `ModelForm` the admin uses |

Always wrap the text in `gettext_lazy` (`_()`), and add the Italian string to the catalog
(`python tools/i18n_catalog.py extract`, then translate; `check` must pass).

## What to write

One or two sentences, plain words, written for a vendor who is not technical. No jargon, no field
names, no acronyms without saying what they mean. Say:

1. **What to enter.** The kind of thing, in the vendor's words.
2. **Why it matters.** Where it shows up or what it changes.
3. **An example or a limit.** A short sample, a maximum length, a format.

A required field also says **what happens if it is left empty**.

Examples are in English; the Italian text is a translation of them.

| Field | Bad | Good |
| --- | --- | --- |
| `alt_text` | "Alt text." | "Describe the picture in a few words, for people who cannot see it. Example: 'Red ceramic mug on a wooden table'." |
| `shipping_policy` | "Shipping policy field." | "Tell buyers how and when you ship. It is shown on every product page. Leave it empty and buyers see the platform's default text." |
| `summary` | "Summary of the article, max 300 chars." | "A short teaser shown on the blog list, up to 300 characters. If you leave it empty, the list shows the first lines of the article." |
| `cta_label` | "CTA label." | "The words on the button, for example 'Apply now'. If you leave it empty the button says 'Apply now'." |
| `carrier` (required) | "The carrier." | "The courier that carries the parcel, for example 'DHL'. Required: the buyer cannot follow the parcel without it." |

Do not repeat the label ("Name: the name"). Do not describe the database ("Foreign key to
Vendor"). Do not promise what the code does not do.

## When a field needs no help

A field whose label already says everything, with nothing to limit or explain, goes on the
allow-list in `tests/unit/field_help_allowlist.py`. Each entry has a reason, and an entry without a
reason fails the sweep. `title` is on it by field name. Add a single field by its full key to
`ALLOWED_KEYS` (the sweep fails if the key matches no field). When in doubt, write the help text.

## The sweep and the baselines

`tests/unit/test_field_help_sweep.py` runs without a database. It reads, from every app under `apps/`:

- Wagtail page and snippet models through their panels, including inline panels;
- StreamField block definitions;
- the forms of the Manager (and the vendor, events and compliance forms staff or vendors fill in);
- the Django admin `ModelAdmin` forms and inlines.

It fails on an editor-facing field without help text and prints its key. Keys look like
`wagtail:cms.blogpostpage.summary`, `block:events.eventpage.body.gallery.caption`,
`form:apps.manager.forms.ShipmentForm.carrier` and `admin:catalog.product.name`.
Fields the framework owns (Wagtail's own page fields, for example) are skipped: they have the
framework's help.

A second check fails when a help text is not marked for translation, or has no Italian entry in the
catalog (`locale/it/LC_MESSAGES/django.po`, read through `tools/i18n_catalog.py`).

Fields that fail today are listed in two files next to the test. Their first line is the count:

- `tests/unit/field_help_baseline.txt` (no help text);
- `tests/unit/field_help_i18n_baseline.txt` (help text not translated).

The baselines only shrink:

- a new field without help text, or with untranslated help, fails the test (it is not in the baseline);
- a baselined field that now has help also fails, until you delete its line;
- the count on the first line must match the lines below it.

To prune after fixing fields, run

```sh
FIELD_HELP_UPDATE_BASELINE=1 uv run pytest tests/unit/test_field_help_sweep.py -n 0
```

which rewrites both files with the fields that still fail and refuses to add a new one. Commit the
shorter files. ARC.31 empties them, area by area.
