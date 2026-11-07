"""CMS.5: versioned legal pages, one version history per language (Q-42).

Done when: "Legal page versions kept per language, so a translated legal page has its own version
and effective date (Q-42)". Every publish that changes the title, the text or the effective date
records an append-only `LegalPageVersion` of that page, which is one language: a translation is
its own page (same `translation_key`, another locale) with its own versions. The page shows the
version in force, its effective date and the archive of earlier versions.
"""

import datetime as dt
import re
import secrets

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import ProtectedError
from django.test import Client
from wagtail.models import Locale

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cms.models import LegalPage, LegalPageVersion
from apps.cms.services import SeedReport, record_legal_version
from apps.cms.tests.factories import LegalPageFactory, LegalPageVersionFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

ACTION = "cms.legal_page_version.recorded"


def _editor() -> User:
    user: User = UserFactory.create(
        email="legal-editor@example.com", password=secrets.token_urlsafe(16), superuser=True
    )
    return user


def _publish(page: LegalPage, user: User | None = None) -> LegalPage:
    page.save_revision(user=user).publish(user=user)
    page.refresh_from_db()
    return page


def _italian(page: LegalPage, title: str, effective_date: dt.date) -> LegalPage:
    translation: LegalPage = page.copy_for_translation(Locale.objects.get(language_code="it"))
    translation = translation.specific
    translation.title = title
    translation.slug = "condizioni"
    translation.effective_date = effective_date
    translation.body = [("paragraph", "<p>Condizioni di vendita.</p>")]
    return _publish(translation)


# --- Versions recorded on publish


def test_publishing_a_legal_page_records_its_first_version(seeded: SeedReport) -> None:
    editor = _editor()
    page = _publish(LegalPageFactory.create(title="Terms of sale", live=False), editor)

    version = page.versions.get()
    assert version.number == 1
    assert version.title == "Terms of sale"
    assert version.language_code == "en"
    assert version.effective_date == dt.date(2027, 1, 1)
    assert "Sample terms." in str(version.body)
    assert version.published_by == f"staff:{editor.get_username()}"


def test_each_recorded_version_is_audited(seeded: SeedReport) -> None:
    editor = _editor()
    page = _publish(LegalPageFactory.create(live=False), editor)

    entry = AuditLog.objects.get(action=ACTION)
    assert entry.actor_id == editor.pk
    assert entry.target_model == "cms.legalpageversion"
    assert entry.target_key == f"{page.pk}/1"
    assert entry.before is None
    after = entry.after or {}
    assert after["effective_date"] == "2027-01-01"
    assert after["language_code"] == "en"


def test_republishing_the_same_text_records_no_new_version(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    _publish(page)
    assert page.versions.count() == 1
    assert AuditLog.objects.filter(action=ACTION).count() == 1


def test_a_changed_text_is_a_new_version_and_the_old_one_is_kept(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    page.body = [("paragraph", "<p>New terms.</p>")]
    page.effective_date = dt.date(2027, 6, 1)
    _publish(page)

    first, second = page.versions.order_by("number")
    assert (first.number, first.effective_date) == (1, dt.date(2027, 1, 1))
    assert "Sample terms." in str(first.body)
    assert (second.number, second.effective_date) == (2, dt.date(2027, 6, 1))
    assert "New terms." in str(second.body)


def test_a_new_version_cannot_take_effect_before_the_last_one(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    page.effective_date = dt.date(2026, 12, 31)
    with pytest.raises(ValidationError) as excinfo:
        page.save_revision()
    assert "effective_date" in excinfo.value.message_dict
    assert page.versions.count() == 1


def test_a_dry_run_reports_the_version_without_recording_it(seeded: SeedReport) -> None:
    page = LegalPageFactory.create()
    ctx = AuditContext(actor_label="system", source=AuditLog.Source.SYSTEM)

    changeset = record_legal_version(page, ctx=ctx, dry_run=True)

    assert [change.key for change in changeset.changes] == [f"{page.pk}/1"]
    assert not LegalPageVersion.objects.exists()
    assert not AuditLog.objects.filter(action=ACTION).exists()


# --- One history per language (Q-42)


def test_a_translation_has_its_own_versions_and_effective_date(seeded: SeedReport) -> None:
    english = _publish(LegalPageFactory.create(title="Terms of sale", live=False))
    italian = _italian(english, "Condizioni di vendita", dt.date(2027, 3, 1))

    assert italian.translation_key == english.translation_key
    assert [(v.language_code, v.number, v.effective_date) for v in italian.versions.all()] == [
        ("it", 1, dt.date(2027, 3, 1))
    ]
    assert [(v.language_code, v.number, v.effective_date) for v in english.versions.all()] == [
        ("en", 1, dt.date(2027, 1, 1))
    ]

    italian.body = [("paragraph", "<p>Condizioni aggiornate.</p>")]
    italian.effective_date = dt.date(2027, 9, 1)
    _publish(italian)
    assert italian.versions.count() == 2
    assert english.versions.count() == 1


def test_the_translated_page_shows_its_own_effective_date(
    client: Client, seeded: SeedReport
) -> None:
    english = _publish(LegalPageFactory.create(title="Terms of sale", live=False))
    _italian(english, "Condizioni di vendita", dt.date(2027, 3, 1))

    html = client.get("/it/condizioni/").content.decode()
    assert '<time datetime="2027-03-01">' in html
    assert '<time datetime="2027-01-01">' not in html


# --- Append-only archive


def test_versions_are_append_only_in_the_database(seeded: SeedReport) -> None:
    version = LegalPageVersionFactory.create()
    with pytest.raises(PermissionError):
        version.save()
    rows = LegalPageVersion.objects.filter(pk=version.pk)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.update(title="Changed")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.delete()
    assert rows.get().title == version.title


def test_a_legal_page_with_versions_cannot_be_deleted(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    with pytest.raises(ProtectedError), transaction.atomic():
        page.delete()
    assert LegalPage.objects.filter(pk=page.pk).exists()


# --- What visitors see


def test_the_page_shows_the_version_in_force_and_the_archive(
    client: Client, seeded: SeedReport
) -> None:
    page = _publish(LegalPageFactory.create(title="Terms of sale", live=False))
    page.body = [("paragraph", "<p>New terms.</p>")]
    page.effective_date = dt.date(2025, 6, 1)  # rejected: before version 1
    with pytest.raises(ValidationError):
        page.save_revision()
    page.effective_date = dt.date(2027, 6, 1)
    _publish(page)

    response = client.get("/terms-of-sale/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<h1>Terms of sale</h1>" in html
    assert '<time datetime="2027-06-01">' in html
    assert "New terms." in html
    assert 'href="/terms-of-sale/versions/1/"' in html

    archived = client.get("/terms-of-sale/versions/1/")
    assert archived.status_code == 200
    old = archived.content.decode()
    assert "Sample terms." in old
    assert "New terms." not in old
    assert '<time datetime="2027-01-01">' in old
    assert 'href="/terms-of-sale/"' in old


def test_an_unknown_version_is_not_found(client: Client, seeded: SeedReport) -> None:
    _publish(LegalPageFactory.create(title="Terms of sale", live=False))
    assert client.get("/terms-of-sale/versions/9/").status_code == 404


def test_a_version_not_yet_in_force_points_to_the_one_that_applies(
    client: Client, seeded: SeedReport
) -> None:
    page = LegalPageFactory.create(title="Privacy", live=False)
    page.effective_date = dt.date(2000, 1, 1)
    page = _publish(page)
    page.body = [("paragraph", "<p>Future privacy text.</p>")]
    page.effective_date = dt.date(2999, 1, 1)
    _publish(page)

    html = client.get("/privacy/").content.decode()
    assert '<time datetime="2999-01-01">' in html
    assert re.search(r'class="legal-notice"[^>]*>.*href="/privacy/versions/1/"', html, re.S)


def test_legal_pages_go_under_a_home_page() -> None:
    assert LegalPage.parent_page_types == ["cms.HomePage"]
    assert LegalPage.subpage_types == []
