"""SEC.5 item 8: hostile input in every Django admin form and every Wagtail page form.

The forms are found programmatically (the admin registry, the Wagtail page types), so a new
admin or page type is covered without touching this file. For each, a staff user submits the
`tests.hostile_input` corpus in every free-text field:

- the request never fails (the test client re-raises any exception a view lets through);
- NUL characters and text over a field's `max_length` are refused on the form;
- a value that is saved is shown escaped on the change list and the change page.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.test import Client, RequestFactory
from django.urls import reverse
from wagtail.blocks.base import BlockField
from wagtail.models import Page, get_page_models

from apps.accounts.models import User
from tests import hostile_input as hi

pytestmark = pytest.mark.django_db

#: One value per family; the full corpus runs against the pure parsers (unit suite).
FORM_VALUES = (
    "<script>alert(1)</script>",
    "'><img src=x onerror=alert(1)>",
    "{{ settings.SECRET_KEY }}{% debug %}",
    "'; DROP TABLE vendors_vendor; --",
    "$(id) `id`",
    hi.WORD_HTML[0],
    "invoice‮gpj.exe zero​width",
    "Città \U0001f468‍\U0001f469‍\U0001f467",
    '=HYPERLINK("https://example.invalid","x")',
    "subject\r\nBcc: someone@example.invalid",
    "1.234,50 €",
)

#: What must never come back unescaped in a page that shows a saved value.
RAW_MARKUP = ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>")


def _registered() -> list[tuple[type[models.Model], admin.ModelAdmin[Any]]]:
    return sorted(admin.site._registry.items(), key=lambda item: item[0]._meta.label)


def _free_text(form_class: Any) -> Iterator[tuple[str, forms.CharField]]:
    for name, field in form_class.base_fields.items():
        if isinstance(field, forms.CharField) and not isinstance(field.widget, forms.HiddenInput):
            yield name, field


@pytest.fixture
def staff(superuser: User, client: Client, mfa_login: Any) -> Client:
    return mfa_login(client, superuser)  # type: ignore[no-any-return]


def _form_class(model_admin: admin.ModelAdmin[Any], user: User) -> Any:
    request = RequestFactory().get("/")
    request.user = user
    if not model_admin.has_add_permission(request):
        return None
    return model_admin.get_form(request, obj=None)


@pytest.mark.parametrize(
    ("model", "model_admin"),
    _registered(),
    ids=[model._meta.label for model, _ in _registered()],
)
def test_every_admin_add_form_survives_the_corpus(
    model: type[models.Model], model_admin: admin.ModelAdmin[Any], staff: Client, superuser: User
) -> None:
    form_class = _form_class(model_admin, superuser)
    if form_class is None:
        pytest.skip(f"{model._meta.label}: no add permission (read-only in the admin)")
    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_add")
    fields = dict(_free_text(form_class))

    for value in FORM_VALUES:
        response = staff.post(url, {name: value for name in fields})
        assert response.status_code in (200, 302), (model._meta.label, value)
        if response.status_code == 302:
            _assert_shown_escaped(staff, model, model_admin, value)


def _assert_shown_escaped(
    staff: Client, model: type[models.Model], model_admin: admin.ModelAdmin[Any], value: str
) -> None:
    opts = model._meta
    changelist = staff.get(reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"))
    assert changelist.status_code == 200
    obj = model._default_manager.order_by("-pk").first()
    assert obj is not None
    change = staff.get(reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk]))
    assert change.status_code == 200
    for page in (changelist, change):
        html = page.content.decode()
        assert not [raw for raw in RAW_MARKUP if raw in value and raw in html], opts.label


@pytest.mark.parametrize(
    ("model", "model_admin"),
    _registered(),
    ids=[model._meta.label for model, _ in _registered()],
)
def test_admin_forms_refuse_nul_and_over_long_text(
    model: type[models.Model], model_admin: admin.ModelAdmin[Any], superuser: User
) -> None:
    form_class = _form_class(model_admin, superuser)
    if form_class is None:
        pytest.skip(f"{model._meta.label}: no add permission")
    for name, field in _free_text(form_class):
        form = form_class(data={name: "nul\x00inside"})
        form.is_valid()
        assert name in form.errors, (model._meta.label, name, "NUL")
        if field.max_length:
            _, over = hi.at_and_over(field.max_length)
            form = form_class(data={name: over})
            form.is_valid()
            assert name in form.errors, (model._meta.label, name, "max_length")


# --- Wagtail page forms ---------------------------------------------------------------------


def _stream_fields(form_class: Any) -> list[str]:
    return [name for name, field in form_class.base_fields.items() if isinstance(field, BlockField)]


def _page_models() -> list[type[Page]]:
    return sorted(
        (m for m in get_page_models() if m.__module__.startswith("apps.") and m.is_creatable),
        key=lambda m: m._meta.label,
    )


@pytest.mark.parametrize("page_model", _page_models(), ids=lambda m: m._meta.label)
def test_every_wagtail_page_form_survives_the_corpus(
    page_model: type[Page], superuser: User
) -> None:
    form_class = page_model.get_edit_handler().get_form_class()
    parent = Page.get_first_root_node()
    for value in (*FORM_VALUES, "nul\x00inside"):
        data = {name: value for name, _ in _free_text(form_class)}
        data |= {f"{name}-count": "0" for name in _stream_fields(form_class)}
        form = form_class(data=data, instance=page_model(), parent_page=parent, for_user=superuser)
        valid = form.is_valid()
        if "\x00" in value:
            assert not valid
        for name, field in _free_text(form_class):
            if field.max_length and name in form.cleaned_data:
                assert len(form.cleaned_data[name]) <= field.max_length


# --- Stored hostile text, shown by the admin ------------------------------------------------

#: Stored by a factory, so the check does not depend on each form's other required fields.
STORED = "<script>alert(1)</script>‮{{ 7*7 }}"


def _factories() -> dict[type[models.Model], Any]:
    from tests.factory_registry import app_factories, factory_model

    found: dict[type[models.Model], Any] = {}
    for factory_class in app_factories():
        found.setdefault(factory_model(factory_class), factory_class)
    return found


def _text_overrides(model: type[models.Model]) -> dict[str, str]:
    overrides = {}
    for field in model._meta.concrete_fields:
        if (
            isinstance(field, models.CharField | models.TextField)
            and not field.choices
            and not field.unique
            and field.editable
            and not isinstance(field, models.EmailField | models.URLField | models.SlugField)
        ):
            overrides[field.name] = STORED[: field.max_length] if field.max_length else STORED
    return overrides


@pytest.mark.parametrize(
    ("model", "model_admin"),
    _registered(),
    ids=[model._meta.label for model, _ in _registered()],
)
def test_every_admin_shows_stored_hostile_text_escaped(
    model: type[models.Model], model_admin: admin.ModelAdmin[Any], staff: Client
) -> None:
    factory_class = _factories().get(model)
    overrides = _text_overrides(model)
    if factory_class is None or not overrides:
        pytest.skip(f"{model._meta.label}: no factory or no free-text column")
    stored = [
        obj for name, value in overrides.items() if (obj := _create(factory_class, name, value))
    ]
    if not stored:
        pytest.skip(f"{model._meta.label}: every free-text column is validated (codes, countries)")

    opts = model._meta
    pages = [staff.get(reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"))]
    pages += [
        staff.get(reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk]))
        for obj in stored
    ]

    for page in pages:
        assert page.status_code in (200, 302, 403), opts.label
        assert "<script>alert(1)</script>" not in page.content.decode(), opts.label


def _create(factory_class: Any, name: str, value: str) -> models.Model | None:
    """One row with `value` in column `name`, or None when the model refuses it."""
    try:
        with transaction.atomic():
            obj: models.Model = factory_class.create(**{name: value})
    except (IntegrityError, ValidationError, ValueError):
        return None
    return obj
