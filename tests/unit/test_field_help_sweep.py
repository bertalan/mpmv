"""ARC.30: every editor-facing field has plain-language, translated help text.

No database: the sweep reads model, panel, block, form and admin definitions. Fields that fail
today sit in `field_help_baseline.txt` (no help) and `field_help_i18n_baseline.txt` (help not
translated), counted, and the files only shrink: ARC.31 empties them. See docs/dev/field-help.md.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from django import forms
from django.db import models
from django.test.utils import isolate_apps
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.admin.panels import FieldPanel

from tests.unit import field_help_allowlist as allowlist
from tests.unit import field_help_sweep as sweep

UPDATE = os.environ.get("FIELD_HELP_UPDATE_BASELINE") == "1"


@pytest.fixture(scope="module")
def items() -> list[sweep.FieldHelp]:
    return sweep.collect_all()


def _check_baseline(path: Path, current: list[str], what: str) -> None:
    declared, listed = sweep.read_baseline(path)
    print(f"{path.name}: {len(listed)} fields baselined, {len(current)} failing now")
    if UPDATE:
        assert set(current) <= set(listed), (
            f"the baseline cannot grow: {sorted(set(current) - set(listed))[:5]}"
        )
        sweep.write_baseline(path, current, what)
        return
    assert declared == len(listed), f"{path.name} says count {declared} but lists {len(listed)}"
    new = sorted(set(current) - set(listed))
    assert not new, f"{len(new)} {what}; add help text (docs/dev/field-help.md), e.g. {new[:20]}"
    fixed = sorted(set(listed) - set(current))
    assert not fixed, (
        f"{len(fixed)} baselined fields are fine now; delete them from {path.name} "
        f"(FIELD_HELP_UPDATE_BASELINE=1 prunes it), e.g. {fixed[:20]}"
    )


# -- The real sweep ----------------------------------------------------------------------------


def test_every_editor_field_has_help_text_or_is_baselined(items: list[sweep.FieldHelp]) -> None:
    _check_baseline(
        sweep.BASELINE,
        sweep.missing_help(items),
        "editor-facing fields have no help text",
    )


def test_every_help_text_is_translated_or_is_baselined(items: list[sweep.FieldHelp]) -> None:
    _check_baseline(
        sweep.I18N_BASELINE,
        sweep.untranslated(items),
        "help texts are not marked for translation or not in the Italian catalog",
    )


def test_the_sweep_sees_every_surface(items: list[sweep.FieldHelp]) -> None:
    prefixes = {i.key.split(":", 1)[0] for i in items}
    assert prefixes == {"wagtail", "block", "form", "admin"}
    keys = {i.key for i in items}
    assert any(k.startswith("wagtail:events.eventpage.") for k in keys)
    assert any(k.startswith("wagtail:cms.navigationmenu.") for k in keys)
    assert any(k.startswith("form:apps.manager.forms.") for k in keys)
    assert any(k.startswith("admin:catalog.product.") for k in keys)
    assert any(k.startswith("block:cms.") for k in keys)


def test_the_allow_list_is_reasoned_and_current(items: list[sweep.FieldHelp]) -> None:
    assert sweep.allowlist_problems(i.key for i in items) == []


def test_a_baseline_never_lists_an_allow_listed_field() -> None:
    for path in (sweep.BASELINE, sweep.I18N_BASELINE):
        _, listed = sweep.read_baseline(path)
        assert [k for k in listed if sweep.is_allowed(k)] == []


# -- Seeds: the sweep catches a field added without help ----------------------------------------


@pytest.fixture
def seed_models() -> Iterator[dict[str, type[models.Model]]]:
    with isolate_apps("apps.cms"):

        class SeedSnippet(models.Model):
            __module__ = "apps.cms.models"
            name = models.CharField(max_length=20)
            hint = models.CharField(max_length=20, help_text=_("Shown under the name."))
            title = models.CharField(max_length=20)
            panels = [FieldPanel("name"), FieldPanel("hint"), FieldPanel("title")]

            class Meta:
                app_label = "cms"

            def __str__(self) -> str:
                return str(self.name)

        yield {"snippet": SeedSnippet}


def test_a_seed_model_field_without_help_fails(seed_models: dict[str, type[models.Model]]) -> None:
    found = sweep.collect_panels([seed_models["snippet"]])
    assert sweep.missing_help(found) == ["wagtail:cms.seedsnippet.name"]


def test_a_panel_help_text_counts(seed_models: dict[str, type[models.Model]]) -> None:
    model = seed_models["snippet"]
    # get_edit_handler caches per model; build a fresh handler for the changed panels
    from wagtail.admin.panels import ObjectList

    handler = ObjectList([FieldPanel("name", help_text=_("The name on the label."))]).bind_to_model(
        model
    )
    out: list[sweep.FieldHelp] = []
    sweep._walk_panel(handler, out, set())
    assert sweep.missing_help(out) == []


def test_a_seed_block_field_without_help_fails() -> None:
    block = blocks.StructBlock(
        [
            ("text", blocks.CharBlock()),
            ("note", blocks.CharBlock(help_text=_("Shown in small print."))),
            ("items", blocks.ListBlock(blocks.CharBlock())),
        ]
    )
    out: list[sweep.FieldHelp] = []
    sweep.walk_block(block, "cms.seedpage.body", out)
    assert sweep.missing_help(out) == [
        "block:cms.seedpage.body.items.item",
        "block:cms.seedpage.body.text",
    ]


def test_a_list_block_help_text_covers_its_items() -> None:
    block = blocks.ListBlock(blocks.CharBlock(), help_text=_("One line per item."))
    out: list[sweep.FieldHelp] = []
    sweep.walk_block(block, "cms.seedpage.links", out)
    assert sweep.missing_help(out) == []


def test_a_seed_form_field_without_help_fails() -> None:
    class SeedForm(forms.Form):
        carrier = forms.CharField()
        note = forms.CharField(help_text=_("Printed on the label."))
        hidden = forms.CharField(widget=forms.HiddenInput)

    out = sweep.collect_form(SeedForm, "form:seed.SeedForm")
    assert sweep.missing_help(out) == ["form:seed.SeedForm.carrier"]


def test_a_blank_help_text_is_no_help_text() -> None:
    assert sweep.missing_help([sweep.FieldHelp("form:seed.Form.x", "   ")]) == ["form:seed.Form.x"]


def test_a_framework_owned_field_is_not_ours_to_fix() -> None:
    assert sweep.missing_help([sweep.FieldHelp("wagtail:cms.homepage.slug", "", owned=False)]) == []


def test_an_allow_listed_field_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "form:seed.SeedForm.title"
    assert sweep.missing_help([sweep.FieldHelp(key, "")]) == []
    monkeypatch.setitem(
        allowlist.ALLOWED_KEYS, "form:seed.SeedForm.code", "A code printed on the card."
    )
    assert sweep.missing_help([sweep.FieldHelp("form:seed.SeedForm.code", "")]) == []


def test_an_allow_list_entry_without_a_reason_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(allowlist.ALLOWED_KEYS, "form:seed.SeedForm.code", "  ")
    problems = sweep.allowlist_problems(["form:seed.SeedForm.code"])
    assert problems == ["form:seed.SeedForm.code has no reason"]
    monkeypatch.setitem(allowlist.ALLOWED_FIELD_NAMES, "label", "")
    assert "field name 'label' has no reason" in sweep.allowlist_problems([])


def test_an_allow_list_entry_for_a_missing_field_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(allowlist.ALLOWED_KEYS, "form:gone.Form.x", "Self-explanatory.")
    assert sweep.allowlist_problems([]) == ["form:gone.Form.x matches no field (remove it)"]


# -- Translation check ---------------------------------------------------------------------------


def test_a_plain_string_help_text_is_not_marked_for_translation() -> None:
    plain = sweep.FieldHelp("form:seed.Form.x", "Printed on the label.")
    assert sweep.untranslated([plain]) == ["form:seed.Form.x"]


def test_a_lazy_help_text_missing_from_the_italian_catalog_fails() -> None:
    lazy = sweep.FieldHelp("form:seed.Form.x", _("A sentence no catalog has ever seen."))
    assert sweep.untranslated([lazy]) == ["form:seed.Form.x"]


def test_a_help_text_in_the_italian_catalog_passes() -> None:
    import polib

    po = polib.pofile(str(sweep.catalog._po_path("it")))
    msgid = next(e.msgid for e in po if e.msgstr and "%" not in e.msgid and not e.msgctxt)
    ok = sweep.FieldHelp("form:seed.Form.x", _(msgid))
    assert sweep.untranslated([ok]) == []


# -- Baseline files ------------------------------------------------------------------------------


def test_a_baseline_with_a_wrong_count_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    path.write_text("# count: 5\na.b.c\n", encoding="utf-8")
    declared, listed = sweep.read_baseline(path)
    assert (declared, len(listed)) == (5, 1)


def test_the_baseline_files_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    sweep.write_baseline(path, ["b.b.b", "a.a.a", "a.a.a"], "test")
    assert sweep.read_baseline(path) == (2, ["a.a.a", "b.b.b"])
