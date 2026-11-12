"""Collectors for the field-help sweep (ARC.30): no database, only definitions.

Each collector returns `FieldHelp` items, one per editor-facing field, with a stable `key`:

    wagtail:<app>.<model>.<field>          page and snippet panels (and their inline panels)
    block:<app>.<model>.<field>.<path>     StreamField block definitions
    form:<module>.<Form>.<field>           Manager and other staff or vendor forms
    admin:<app>.<model>.<field>            Django admin ModelAdmin forms and inlines

`help` is the help text as written (a lazy string, a plain string or empty). Fields that the
framework owns (Wagtail's `slug`, Django's `username`, ...) have `owned=False`: they carry the
framework's own help and are not ours to translate.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polib
from django import forms
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from tests.unit.field_help_allowlist import ALLOWED_FIELD_NAMES, ALLOWED_KEYS

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("i18n_catalog", ROOT / "tools" / "i18n_catalog.py")
assert _spec is not None
assert _spec.loader is not None
catalog = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("i18n_catalog", catalog)
_spec.loader.exec_module(catalog)

PROJECT_PREFIX = "apps."

# Modules whose form classes are filled in by a vendor or by platform staff outside the admins.
FORM_MODULES = (
    "apps.manager.forms",
    "apps.manager.media",
    "apps.vendors.forms",
    "apps.compliance.forms",
    "apps.events.admin_views",
)


@dataclass(frozen=True)
class FieldHelp:
    key: str
    help: Any  # str, lazy str or ""
    owned: bool = True

    @property
    def text(self) -> str:
        return str(self.help or "").strip()

    @property
    def lazy(self) -> bool:
        from django.utils.functional import Promise

        return isinstance(self.help, Promise)


def _owned(field: models.Field[Any, Any]) -> bool:
    return field.model.__module__.startswith(PROJECT_PREFIX)


def _label(model: type[models.Model]) -> str:
    return f"{model._meta.app_label}.{model._meta.model_name}"


def _project_models() -> list[type[models.Model]]:
    return [m for m in apps.get_models() if m.__module__.startswith(PROJECT_PREFIX)]


# -- Wagtail panels ----------------------------------------------------------------------------


def _walk_panel(panel: Any, out: list[FieldHelp], seen: set[str]) -> None:
    from wagtail.admin.panels import FieldPanel

    child_handler = getattr(panel, "child_edit_handler", None)
    if child_handler is not None:  # InlinePanel: the related model's own panels
        _walk_panel(child_handler, out, seen)
        return
    if isinstance(panel, FieldPanel):
        model = panel.model
        try:
            field = panel.db_field
        except FieldDoesNotExist:
            field = None
        key = f"wagtail:{_label(model)}.{panel.field_name}"
        if key in seen:
            return
        seen.add(key)
        help_text = panel.help_text or (field.help_text if field is not None else "")
        out.append(FieldHelp(key, help_text, owned=field is None or _owned(field)))
        return
    for child in getattr(panel, "children", ()):
        _walk_panel(child, out, seen)


def panel_models() -> list[type[models.Model]]:
    from wagtail.models import Page
    from wagtail.snippets.models import get_snippet_models

    pages = [m for m in _project_models() if issubclass(m, Page)]
    snippets = [m for m in get_snippet_models() if m.__module__.startswith(PROJECT_PREFIX)]
    return sorted({*pages, *snippets}, key=_label)


def collect_panels(model_list: Iterable[type[models.Model]] | None = None) -> list[FieldHelp]:
    from wagtail.admin.panels import get_edit_handler

    out: list[FieldHelp] = []
    seen: set[str] = set()
    for model in model_list if model_list is not None else panel_models():
        _walk_panel(get_edit_handler(model), out, seen)
    return out


# -- StreamField blocks ------------------------------------------------------------------------


def _block_help(block: Any) -> Any:
    """A block's help text: on its meta, or on the form field of a field block."""
    return getattr(block.meta, "help_text", "") or getattr(
        getattr(block, "field", None), "help_text", ""
    )


def walk_block(block: Any, path: str, out: list[FieldHelp], inherited: Any = "") -> None:
    from wagtail import blocks

    if isinstance(block, blocks.StaticBlock):
        return
    if isinstance(block, blocks.StructBlock | blocks.StreamBlock):
        for name, child in block.child_blocks.items():
            walk_block(child, f"{path}.{name}", out)
    elif isinstance(block, blocks.ListBlock):
        walk_block(block.child_block, f"{path}.item", out, _block_help(block) or inherited)
    else:
        out.append(FieldHelp(f"block:{path}", _block_help(block) or inherited))


def collect_blocks(model_list: Iterable[type[models.Model]] | None = None) -> list[FieldHelp]:
    from wagtail.fields import StreamField

    out: list[FieldHelp] = []
    for model in model_list if model_list is not None else _project_models():
        for field in model._meta.local_fields:
            if isinstance(field, StreamField):
                walk_block(field.stream_block, f"{_label(model)}.{field.name}", out)
    return out


# -- Forms -------------------------------------------------------------------------------------


def collect_form(form_class: type[forms.BaseForm], key_prefix: str) -> list[FieldHelp]:
    meta_model = getattr(getattr(form_class, "_meta", None), "model", None)
    out: list[FieldHelp] = []
    for name, field in form_class.base_fields.items():  # type: ignore[attr-defined]
        if field.widget.is_hidden:
            continue
        owned = True
        if meta_model is not None:
            try:
                owned = _owned(meta_model._meta.get_field(name))
            except FieldDoesNotExist:
                owned = True
        out.append(FieldHelp(f"{key_prefix}.{name}", field.help_text, owned=owned))
    return out


def form_classes() -> list[type[forms.BaseForm]]:
    found: list[type[forms.BaseForm]] = []
    for module_name in FORM_MODULES:
        module = importlib.import_module(module_name)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, forms.BaseForm)
                and obj.__module__ == module_name
                and obj.base_fields  # type: ignore[attr-defined]
            ):
                found.append(obj)
    return found


def collect_forms(form_list: Iterable[type[forms.BaseForm]] | None = None) -> list[FieldHelp]:
    out: list[FieldHelp] = []
    for form_class in form_list if form_list is not None else form_classes():
        out += collect_form(form_class, f"form:{form_class.__module__}.{form_class.__name__}")
    return out


# -- Django admin ------------------------------------------------------------------------------


def _staff_request() -> Any:
    """A request stand-in whose user may do anything, so `get_form` needs no database."""
    user = SimpleNamespace(
        is_active=True,
        is_authenticated=True,
        is_verified=lambda: True,
        is_staff=True,
        is_superuser=True,
        pk=1,
        holds_all_platform_roles=True,  # CORE.18 section checks answer without a database
        has_perm=lambda *a, **k: True,
        has_perms=lambda *a, **k: True,
        has_module_perms=lambda *a, **k: True,
    )
    session = {"mfa_verified_at": time.time()}  # fresh step-up, so every field is offered
    return SimpleNamespace(user=user, GET={}, POST={}, META={}, method="GET", session=session)


def collect_admin() -> list[FieldHelp]:
    from django.contrib import admin

    request = _staff_request()
    out: list[FieldHelp] = []
    seen: set[str] = set()
    sites = {admin.site, *(s for s in _all_sites())}
    for site in sites:
        for model, model_admin in site._registry.items():
            if not model.__module__.startswith(PROJECT_PREFIX):
                continue
            pairs: list[tuple[type[models.Model], Any]] = [(model, model_admin)]
            pairs += [
                (inline.model, inline) for inline in model_admin.get_inline_instances(request)
            ]
            for form_model, handler in pairs:
                form_class = (
                    handler.get_formset(request).form
                    if handler is not model_admin
                    else handler.get_form(request)
                )
                for item in collect_form(form_class, f"admin:{_label(form_model)}"):
                    if item.key not in seen:
                        seen.add(item.key)
                        out.append(item)
    return out


def _all_sites() -> list[Any]:
    from django.contrib.admin.sites import all_sites

    return list(all_sites)


def collect_all() -> list[FieldHelp]:
    return [*collect_panels(), *collect_blocks(), *collect_forms(), *collect_admin()]


# -- Verdicts ----------------------------------------------------------------------------------

HERE = Path(__file__).parent
BASELINE = HERE / "field_help_baseline.txt"
I18N_BASELINE = HERE / "field_help_i18n_baseline.txt"


def is_allowed(key: str) -> bool:
    return key in ALLOWED_KEYS or key.rsplit(".", 1)[-1] in ALLOWED_FIELD_NAMES


def allowlist_problems(keys: Iterable[str]) -> list[str]:
    """Entries without a reason, and per-key entries that match no field."""
    known = set(keys)
    problems = [
        f"field name {n!r} has no reason"
        for n, why in ALLOWED_FIELD_NAMES.items()
        if not why.strip()
    ]
    for key, why in ALLOWED_KEYS.items():
        if not why.strip():
            problems.append(f"{key} has no reason")
        if key not in known:
            problems.append(f"{key} matches no field (remove it)")
    return problems


def missing_help(items: Iterable[FieldHelp]) -> list[str]:
    """Keys of our editor-facing fields that have no help text and are not allow-listed."""
    return sorted({i.key for i in items if i.owned and not i.text and not is_allowed(i.key)})


def english_source(item: FieldHelp) -> str:
    from django.utils import translation

    with translation.override("en"):
        return str(item.help)


def untranslated(items: Iterable[FieldHelp]) -> list[str]:
    """Keys whose help text is not marked for translation or has no Italian entry.

    Marked means lazy (`gettext_lazy`); in the catalog means the msgid has a non-empty msgstr in
    each `tools/i18n_catalog.py` language. A string another catalog already translates (a Django
    help text on a field we inherit) also counts.
    """
    from django.utils import translation

    catalogs = {}
    for lang in catalog.LANGUAGES:
        po = polib.pofile(str(catalog._po_path(lang)))
        catalogs[lang] = {e.msgid for e in po if e.msgstr}
    bad = set()
    for item in items:
        if not item.owned or not item.text:
            continue
        if not item.lazy:
            bad.add(item.key)
            continue
        source = english_source(item)
        for lang, done in catalogs.items():
            if source in done:
                continue
            with translation.override(lang):
                if str(item.help) == source:
                    bad.add(item.key)
    return sorted(bad)


def read_baseline(path: Path) -> tuple[int, list[str]]:
    """(declared count, keys) of a baseline file; `# count: N` is its first line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    declared = int(lines[0].removeprefix("# count:").strip())
    return declared, [line for line in lines[1:] if line and not line.startswith("#")]


def write_baseline(path: Path, keys: Iterable[str], what: str) -> None:
    ordered = sorted(set(keys))
    body = "\n".join(ordered)
    path.write_text(
        f"# count: {len(ordered)}\n# {what}\n"
        f"# Only shrinks: delete a line when its field is fixed.\n{body}\n",
        encoding="utf-8",
    )
