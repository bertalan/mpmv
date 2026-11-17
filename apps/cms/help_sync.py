"""`sync_help` (ARC.36): create or update the `HelpEntry` rows from the help files.

Idempotent, files win. A row made by this sync carries `source="file"`; a staff row with the same
language, kind and key is taken over (the file replaces it). Staff rows with keys that no file
names are left alone, and so are file rows whose file was removed (reported, never deleted: a
deleted file is a review item). Only languages that exist as Locales are synced. With
`dry_run=True` nothing is written; the `ChangeSet` says what would change.
"""

from collections import defaultdict
from pathlib import Path

from django.db import transaction
from wagtail.models import Locale

from apps.core.changeset import ChangeSet

from . import help_files
from .models import HelpEntry

MODEL = "cms.helpentry"


def _snapshot(title: str, body: str, vendor_guide: bool, source: str) -> dict[str, object]:
    return {"title": title, "body": body, "show_vendor_guide": vendor_guide, "source": source}


def sync_help(*, dry_run: bool = False, root: Path = help_files.HELP_ROOT) -> ChangeSet:
    files = help_files.load_all(root)  # a bad file raises before anything is written
    changes = ChangeSet()
    locales = {loc.language_code: loc for loc in Locale.objects.all()}
    by_entry: dict[tuple[str, str], list[help_files.HelpFile]] = defaultdict(list)
    for file in files:
        by_entry[(file.kind, file.key)].append(file)
    with transaction.atomic():
        for (kind, key), group in sorted(by_entry.items()):
            siblings = list(HelpEntry.objects.filter(kind=kind, key=key))
            translation_key = siblings[0].translation_key if siblings else None
            for file in sorted(group, key=lambda f: f.language):
                locale = locales.get(file.language)
                if locale is None:
                    continue
                body = help_files.to_rich_text(file.markdown)
                after = _snapshot(file.title, body, file.vendor_guide, "file")
                label = f"{file.language}/{kind}/{key or 'general'}"
                row = next((r for r in siblings if r.locale_id == locale.pk), None)
                if row is None:
                    row = HelpEntry(locale=locale, kind=kind, key=key, **after)
                    if translation_key is not None:
                        row.translation_key = translation_key
                    changes.create(MODEL, label, after)
                    if not dry_run:
                        row.save()
                    translation_key = row.translation_key
                    siblings.append(row)
                    continue
                before = _snapshot(row.title, row.body, row.show_vendor_guide, row.source)
                if before != after:
                    changes.update(MODEL, label, before, after)
                    if not dry_run:
                        for name, value in after.items():
                            setattr(row, name, value)
                        row.save()
        if dry_run:
            transaction.set_rollback(True)
    return changes


def orphaned_file_rows(root: Path = help_files.HELP_ROOT) -> list[str]:
    """File-backed rows whose file is gone (reported by the command; never deleted)."""
    wanted = {(f.language, f.kind, f.key) for f in help_files.load_all(root)}
    return sorted(
        f"{row.locale.language_code}/{row.kind}/{row.key or 'general'}"
        for row in HelpEntry.objects.filter(source="file").select_related("locale")
        if (row.locale.language_code, row.kind, row.key) not in wanted
    )
