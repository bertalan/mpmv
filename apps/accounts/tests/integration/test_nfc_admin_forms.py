"""I18N.10: the account admin forms store their text in NFC; secrets stay byte-exact."""

import secrets
import unicodedata

import pytest
from django import forms
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.contrib.auth.models import Group
from django.forms import modelform_factory

from apps.accounts.admin import GroupForm, NFCUserChangeForm, StaffCreationForm
from apps.accounts.forms import ManagerLoginForm, StaffLoginForm
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.nfc_forms import NFCFormMixin

pytestmark = pytest.mark.django_db


def decomposed() -> str:
    """A generated text with an "e" and a combining acute accent."""
    return f"x{secrets.token_hex(4)}e\u0301"


def composed(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def test_the_user_change_form_stores_the_name_in_nfc_and_keeps_the_email() -> None:
    user = UserFactory.create()
    email = f"{secrets.token_hex(4)}@xe\u0301.example"
    name = decomposed()
    form_class = modelform_factory(
        User, form=NFCUserChangeForm, fields=("email", "password", "full_name")
    )
    form = form_class({"email": email, "full_name": name}, instance=user)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["full_name"] == composed(name)
    assert form.cleaned_data["email"] != composed(email)  # kept as typed
    password = user.password
    form.save()
    user.refresh_from_db()
    assert user.full_name == composed(name)
    assert user.password == password


def test_the_group_uniqueness_check_compares_nfc_names() -> None:
    name = decomposed()
    Group.objects.create(name=composed(name))
    form_class = modelform_factory(Group, form=GroupForm, fields=("name",))
    form = form_class({"name": name})
    assert not form.is_valid()
    assert "name" in form.errors

    fresh = decomposed()
    form = form_class({"name": fresh})
    assert form.is_valid(), form.errors
    assert form.save().name == composed(fresh)


def test_passwords_and_exact_fields_are_never_normalised() -> None:
    class Probe(NFCFormMixin, forms.Form):
        nfc_exact_fields = ("token",)
        text = forms.CharField()
        token = forms.CharField()
        secret = forms.CharField(widget=forms.PasswordInput)

    value = decomposed()
    form = Probe({"text": value, "token": value, "secret": value})
    assert form.is_valid()
    assert form.cleaned_data == {"text": composed(value), "token": value, "secret": value}


def test_the_login_and_password_forms_keep_the_password_as_typed() -> None:
    for form_class in (
        StaffLoginForm,
        ManagerLoginForm,
        StaffCreationForm,
        AdminPasswordChangeForm,
    ):
        assert not issubclass(form_class, NFCFormMixin)
    password = f"{secrets.token_urlsafe(12)}e\u0301"
    user = UserFactory.create(password=password)
    assert user.check_password(password)
    assert not user.check_password(composed(password))  # normalising would break login
