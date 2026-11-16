"""I18N.15 (3): removing a language or changing the primary one is a command with redirects.

Dry run by default, a ChangeSet, `AuditLog` entries, prefix redirects for the old URLs; a
settings-only removal or primary change is refused by `core.E914`.
"""

from io import StringIO

import pytest
from django.core import checks
from django.core.management import CommandError, call_command
from django.test import Client

from apps.core.audit import AuditContext
from apps.core.language_checks import check_language_state
from apps.core.language_services import (
    current_language_codes,
    sync_language_state,
)
from apps.core.models import AuditLog, LanguageRedirect, SiteLanguages
from apps.core.tests.factories import LanguageRedirectFactory, SiteLanguagesFactory

pytestmark = pytest.mark.django_db


def _record(codes: list[str]) -> None:
    SiteLanguages.objects.all().delete()
    SiteLanguagesFactory.create(codes=codes)


def test_migrate_records_the_configured_languages() -> None:
    SiteLanguages.objects.all().delete()
    sync_language_state()
    from django.conf import settings

    assert current_language_codes() == [code for code, _ in settings.LANGUAGES]


def test_a_settings_only_removal_is_refused_by_a_check(settings) -> None:  # type: ignore[no-untyped-def]
    settings.LANGUAGES = [("en", "English"), ("it", "Italiano")]
    settings.LANGUAGE_CODE = "en"
    _record(["en", "it", "de"])
    messages = check_language_state(None, databases=["default"])
    assert [m.id for m in messages] == ["core.E914"]
    assert isinstance(messages[0], checks.Error)


def test_a_settings_only_primary_change_is_refused(settings) -> None:  # type: ignore[no-untyped-def]
    settings.LANGUAGES = [("it", "Italiano"), ("en", "English")]
    settings.LANGUAGE_CODE = "it"
    _record(["en", "it"])
    assert [m.id for m in check_language_state(None, databases=["default"])] == ["core.E914"]


def test_adding_a_language_by_settings_is_accepted_and_recorded(settings) -> None:  # type: ignore[no-untyped-def]
    settings.LANGUAGES = [("en", "English"), ("it", "Italiano"), ("de", "Deutsch")]
    settings.LANGUAGE_CODE = "en"
    _record(["en", "it"])
    assert check_language_state(None, databases=["default"]) == []
    sync_language_state()
    assert current_language_codes() == ["en", "it", "de"]


def test_the_check_does_nothing_without_a_database_request() -> None:
    _record(["xx"])
    assert check_language_state(None, databases=None) == []


def test_the_command_is_a_dry_run_by_default() -> None:
    _record(["en", "it", "de"])
    out = StringIO()
    call_command("change_languages", "--to", "en,it", stdout=out)
    assert "Would" in out.getvalue()
    assert current_language_codes() == ["en", "it", "de"]
    assert not LanguageRedirect.objects.exists()
    assert not AuditLog.objects.filter(action="language.change").exists()


def test_apply_removes_a_language_creates_redirects_and_audits() -> None:
    _record(["en", "it", "de"])
    call_command("change_languages", "--to", "en,it", "--apply", stdout=StringIO())
    assert current_language_codes() == ["en", "it"]
    redirect = LanguageRedirect.objects.get()
    assert (redirect.old_prefix, redirect.new_prefix) == ("/de/", "/")
    entry = AuditLog.objects.get(action="language.change", target_model="core.sitelanguages")
    assert entry.source == AuditLog.Source.COMMAND
    assert entry.before == {"codes": ["en", "it", "de"], "primary": "en"}
    assert entry.after == {"codes": ["en", "it"], "primary": "en"}
    assert AuditLog.objects.filter(target_model="core.languageredirect").count() == 1


def test_apply_changes_the_primary_language() -> None:
    _record(["en", "it"])
    call_command("change_languages", "--to", "it,en", "--apply", stdout=StringIO())
    assert current_language_codes() == ["it", "en"]
    assert LanguageRedirect.objects.get().old_prefix == "/it/"


def test_an_unknown_language_is_refused() -> None:
    _record(["en", "it"])
    with pytest.raises(CommandError):
        call_command("change_languages", "--to", "en,xx", "--apply", stdout=StringIO())
    assert current_language_codes() == ["en", "it"]


def test_a_removed_language_url_redirects_to_the_new_one() -> None:
    LanguageRedirectFactory.create(old_prefix="/pt/", new_prefix="/")
    response = Client().get("/pt/no-such-page/?q=1")
    assert response.status_code == 301
    assert response["Location"] == "/no-such-page/?q=1"


def test_a_404_without_a_redirect_stays_404() -> None:
    assert Client().get("/pt/no-such-page/").status_code == 404


def test_re_enabling_a_language_drops_its_old_redirect() -> None:
    _record(["en", "it"])
    LanguageRedirect.objects.create(old_prefix="/de/", new_prefix="/")
    call_command("change_languages", "--to", "en,it,de", "--apply", stdout=StringIO())
    assert not LanguageRedirect.objects.filter(old_prefix="/de/").exists()


def test_service_accepts_a_context_and_dry_run() -> None:
    from apps.core.language_services import change_languages

    _record(["en", "it", "de"])
    ctx = AuditContext(actor_label="test", source=AuditLog.Source.COMMAND)
    changes = change_languages(["en", "de"], ctx=ctx, dry_run=True)
    assert not changes.is_empty
    assert current_language_codes() == ["en", "it", "de"]
