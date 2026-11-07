"""CORE.1: custom User with email login and preferred language."""

import secrets

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

# Generated at runtime: no credential-like literals in the repository (secret scanners).
PASSWORD = secrets.token_urlsafe(16)


def test_auth_user_model_is_the_custom_user() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert get_user_model() is User


def test_create_user_normalizes_email_and_hashes_password() -> None:
    user = User.objects.create_user(email="  Mario.Rossi@Example.COM ", password=PASSWORD)
    assert user.email == "mario.rossi@example.com"
    assert user.password != PASSWORD
    assert user.check_password(PASSWORD)
    assert user.preferred_language == "en"
    assert not user.is_staff
    assert user.public_id is not None


def test_email_is_required() -> None:
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email="", password=PASSWORD)


def test_email_is_unique_regardless_of_case() -> None:
    User.objects.create_user(email="anna@example.com", password=PASSWORD)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(email="ANNA@example.com")


def test_superuser_is_staff_and_superuser() -> None:
    admin = User.objects.create_superuser(email="admin@example.com", password=PASSWORD)
    assert admin.is_staff
    assert admin.is_superuser


def test_superuser_must_be_staff() -> None:
    with pytest.raises(ValueError, match="is_staff"):
        User.objects.create_superuser(email="a@example.com", password=PASSWORD, is_staff=False)


def test_login_with_email_and_password() -> None:
    User.objects.create_user(email="luca@example.com", password=PASSWORD)
    assert authenticate(username="LUCA@example.com", password=PASSWORD) is not None
    assert authenticate(username="luca@example.com", password=PASSWORD + "-wrong") is None


@pytest.mark.parametrize("language", ["en", "it", "de"])
def test_preferred_language_accepts_configured_languages(language: str) -> None:
    user = User(email=f"{language}@example.com", preferred_language=language)
    user.set_unusable_password()
    user.full_clean()


def test_preferred_language_rejects_unknown_languages() -> None:
    user = User(email="fr@example.com", preferred_language="fr")
    user.set_unusable_password()
    with pytest.raises(ValidationError):
        user.full_clean()
