"""CMS.12: app code never writes rich text around the sanitiser (docs/dev/rich-text.md).

`bulk_create`, `bulk_update`, `QuerySet.update` and `loaddata` do not send `pre_save`, so none of
them may write a rich-text field (`update` of other fields is fine). Batch writers call `save()` or
`services.sanitise_rich_text_fields` first.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
BULK = {"bulk_create", "bulk_update"}


def _rich_text_fields() -> dict[str, set[str]]:
    """Model name -> names of its rich-text fields (`RichTextField`, `StreamField`)."""
    from django.apps import apps
    from wagtail.fields import RichTextField, StreamField

    found = {}
    for model in apps.get_models():
        names = {
            f.name for f in model._meta.get_fields() if isinstance(f, RichTextField | StreamField)
        }
        if names:
            found[model.__name__] = names
    return found


def _rich_text_models() -> set[str]:
    return set(_rich_text_fields())


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _offences() -> list[str]:
    fields = _rich_text_fields()
    models = set(fields)
    found = []
    for path in sorted((ROOT / "apps").rglob("*.py")):
        rel = path.relative_to(ROOT)
        if "tests" in rel.parts or "migrations" in rel.parts or path.name.startswith("test_"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            method = node.func.attr
            used = _names(node.func.value) & models
            if method in BULK and used:
                found.append(f"{rel}:{node.lineno} {method} on {sorted(used)}")
            if method == "update" and used:
                written = {k.arg for k in node.keywords}  # None for **kwargs
                rich = set().union(*(fields[m] for m in used))
                if None in written or written & rich:
                    found.append(f"{rel}:{node.lineno} update on {sorted(used)}")
    return found


def test_no_bulk_write_touches_a_model_with_rich_text() -> None:
    assert _offences() == []


def test_the_models_with_rich_text_are_found() -> None:
    assert {"BlogIndexPage", "StandardPage", "EventIndexPage"} <= _rich_text_models()
