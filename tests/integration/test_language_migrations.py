"""I18N.2 / coherence check 2: adding a language is a settings entry, never a migration.

Language choices are a callable (`apps.core.i18n.language_choices`) that migrations reference by
name, so a new entry in settings.LANGUAGES leaves every model state unchanged.
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LANGUAGES_PLUS_PORTUGUESE = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("pt", "Português"),
]


@override_settings(LANGUAGES=LANGUAGES_PLUS_PORTUGUESE)
def test_adding_a_language_in_settings_needs_no_migration() -> None:
    # --check exits with status 1 when any model change has no migration.
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


@override_settings(LANGUAGES=LANGUAGES_PLUS_PORTUGUESE)
def test_a_user_can_prefer_a_language_added_in_settings() -> None:
    user = UserFactory.create(preferred_language="pt")
    user.full_clean()
    user.refresh_from_db()
    assert user.preferred_language == "pt"


def test_a_user_cannot_prefer_an_unconfigured_language() -> None:
    user = UserFactory.build(preferred_language="pt")
    with pytest.raises(ValidationError) as excinfo:
        user.full_clean()
    assert "preferred_language" in excinfo.value.message_dict
