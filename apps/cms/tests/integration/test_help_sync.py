"""ARC.36: `sync_help` makes the help entries from the files, idempotently; files win."""

import datetime as dt
from io import StringIO
from pathlib import Path

import pytest
from django.core.cache import cache
from django.core.management import call_command
from wagtail.models import Locale

from apps.cms import help as help_lookup
from apps.cms import help_files, help_sync
from apps.cms.models import HelpEntry
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")


def write(root: Path, lang: str, kind: str, stem: str, title: str, body: str = "Body.") -> None:
    path = root / lang / kind / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\nlast-checked: 2026-11-17\n\n{body}\n", encoding="utf-8")


def test_the_repository_files_create_the_general_entry_in_five_languages() -> None:
    changes = help_sync.sync_help()
    assert not changes.is_empty
    rows = HelpEntry.objects.filter(kind="general")
    assert {r.locale.language_code for r in rows} == set(LANGS)
    assert all(r.source == "file" for r in rows)
    assert len({r.translation_key for r in rows}) == 1
    assert help_sync.sync_help().is_empty  # a second run changes nothing


def test_editing_a_file_updates_the_entry_and_files_win_over_staff(tmp_path: Path) -> None:
    write(tmp_path, "en", "route", "manager__shop__orders", "Orders")
    write(tmp_path, "it", "route", "manager__shop__orders", "Ordini")
    staff = HelpEntry.objects.create(
        locale=Locale.objects.get(language_code="en"),
        kind="route",
        key="manager:shop:orders",
        title="By hand",
    )
    help_sync.sync_help(root=tmp_path)
    staff.refresh_from_db()
    assert (staff.title, staff.source) == ("Orders", "file")
    italian = HelpEntry.objects.get(key="manager:shop:orders", locale__language_code="it")
    assert italian.title == "Ordini"
    write(tmp_path, "en", "route", "manager__shop__orders", "Orders v2", "New body.")
    changes = help_sync.sync_help(root=tmp_path)
    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    staff.refresh_from_db()
    assert staff.title == "Orders v2"
    assert "New body." in staff.body


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    write(tmp_path, "en", "route", "a__b", "A")
    changes = help_sync.sync_help(dry_run=True, root=tmp_path)
    assert changes.summary()["create"] == 1
    assert not HelpEntry.objects.filter(key="a:b").exists()


def test_a_staff_row_without_a_file_is_left_alone(tmp_path: Path) -> None:
    HelpEntry.objects.create(
        locale=Locale.objects.get(language_code="en"), kind="route", key="x:y", title="Mine"
    )
    help_sync.sync_help(root=tmp_path)
    assert HelpEntry.objects.get(key="x:y").source == "staff"


def test_an_unknown_placeholder_fails_the_sync_and_writes_nothing(tmp_path: Path) -> None:
    write(tmp_path, "en", "route", "good", "Good")
    write(tmp_path, "it", "route", "bad", "Bad", "{{ operator.nope }}")
    with pytest.raises(help_files.HelpFileError):
        help_sync.sync_help(root=tmp_path)
    assert not HelpEntry.objects.filter(key="good").exists()


def test_the_command_reports_and_is_dry_run_aware() -> None:
    out = StringIO()
    call_command("sync_help", "--dry-run", stdout=out)
    assert "would change" in out.getvalue()
    assert not HelpEntry.objects.filter(source="file").exists()
    call_command("sync_help", stdout=out)
    assert HelpEntry.objects.filter(source="file").exists()


def test_a_placeholder_renders_the_operators_current_value() -> None:
    cache.clear()
    PlatformOperatorFactory.create(
        support_email="help@operator.example",
        open_ended=True,
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
    )
    help_sync.sync_help()
    content = help_lookup.help_for("no:such:route", "", "en")
    assert content is not None
    assert "help@operator.example" in content.html
    assert "{{" not in content.html
