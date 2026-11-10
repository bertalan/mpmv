"""I18N.7: the account and MFA pages render in Italian for an Italian user."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login

pytestmark = pytest.mark.django_db

EN = {"accept-language": "en"}


@pytest.fixture
def italian() -> User:
    return UserFactory.create(preferred_language="it")


def test_the_mfa_overview_is_in_italian(client: Client, italian: User) -> None:
    verified_login(client, italian)
    html = client.get(reverse("mfa:overview"), headers=EN).text
    for text in ("Verifica in due passaggi", "I tuoi fattori", "Aggiungi un fattore"):
        assert text in html
    assert "Add a factor" not in html
    assert "non usat" in html  # the unused-codes count, in plural form


def test_the_mfa_setup_and_totp_pages_are_in_italian(client: Client, italian: User) -> None:
    verified_login(client, italian)
    setup = client.get(reverse("mfa:setup"), headers=EN).text
    assert "Configura la verifica in due passaggi" in setup
    assert "Registra una passkey" in setup
    # The passkey script's messages come from the page, not from JavaScript literals.
    assert 'data-text-unsupported="Questo browser non supporta le passkey."' in setup
    totp = client.get(reverse("mfa:totp"), headers=EN).text
    assert "Configura un\u2019app di autenticazione" in totp
    assert "Codice di autenticazione" in totp


def test_the_recovery_codes_page_pluralises_in_italian(client: Client, italian: User) -> None:
    verified_login(client, italian)
    html = client.get(reverse("mfa:recovery_codes"), headers=EN).text
    assert "Codici di recupero" in html
    assert "Genera nuovi codici" in html
