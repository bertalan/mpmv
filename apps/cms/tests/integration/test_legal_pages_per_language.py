"""I18N.9: customer legal and checkout pages in every go-live language (Q-42, spec 09 §9.2).

Done when: "Every one of the five pages is published in all five languages; a go-live readiness
check fails when a legal or checkout page lacks a published translation in a go-live language; a
translation of a legal page shows its own effective date".

Terms of sale, privacy, withdrawal, shipping and returns are `LegalPage` translations made with
wagtail-localize. Each language keeps its own versions and effective date, set by the translator
with `set_legal_effective_date` while the sync stays on. A sync brings the source's text; when that
text is newer than the translation's date, the translation's date moves up to the source's, so a
new text never applies retroactively. The AI draft, the native-speaker review and the legal
counsel check are recorded per language, on the version they reviewed. These pages never fall
back to EN. The texts themselves are human work: the seed only creates placeholders, marked as
drafts until counsel has checked them.
"""

import datetime as dt
import io
import secrets

import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.models import Translation

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cms.engine import GO_LIVE_LEGAL_KINDS, VersionStamp, version_in_force
from apps.cms.models import LegalPage, LegalPageReview
from apps.cms.services import (
    SeedReport,
    legal_readiness,
    record_legal_review,
    seed_site,
    set_legal_effective_date,
    sync_page_translations,
    translate_page,
)
from apps.cms.tests.factories import LegalPageFactory, LegalPageReviewFactory
from apps.core.audit import AuditContext
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

REVIEW_ACTION = "cms.legal_page_review.recorded"
DATE_ACTION = "cms.legal_page.effective_date_set"
CTX = AuditContext(actor_label="command:test", source=AuditLog.Source.COMMAND)
REVIEWED_ON = dt.date(2026, 10, 1)


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation when the transaction commits; a test runs in
    one transaction that never commits, so the hooks run at once here."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.fixture
def editor() -> User:
    user: User = UserFactory.create(superuser=True, password=secrets.token_urlsafe(16))
    return user


def _publish(page: LegalPage) -> LegalPage:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _review(page: LegalPage, *stages: str) -> None:
    version = page.versions.order_by("-number").first()
    assert version is not None
    for stage in stages:
        record_legal_review(
            version, stage, reviewer="A. Reviewer", reviewed_on=REVIEWED_ON, ctx=CTX
        )


def _every_page_in_every_language(*, reviewed: bool = True) -> dict[tuple[str, str], LegalPage]:
    """The five go-live pages, published and (optionally) reviewed in every language."""
    pages: dict[tuple[str, str], LegalPage] = {}
    for kind in GO_LIVE_LEGAL_KINDS:
        english = _publish(LegalPageFactory.create(title=f"Page {kind}", kind=kind, live=False))
        pages[kind, "en"] = english
        for code in configured_languages()[1:]:
            copy: LegalPage = english.copy_for_translation(
                Locale.objects.get(language_code=code)
            ).specific
            copy.title = f"{kind} {code}"
            copy.slug = f"{kind}-{code}"
            pages[kind, code] = _publish(copy)
    if reviewed:
        for page in pages.values():
            _review(page, "native_review", "counsel_check")
    return pages


def _readiness() -> str:
    out = io.StringIO()
    call_command("golive_readiness", stdout=out)
    return out.getvalue()


# --- The readiness check


def test_the_readiness_check_passes_when_every_page_is_published_and_checked(
    seeded: SeedReport,
) -> None:
    _every_page_in_every_language()
    assert legal_readiness() == []
    assert "ready" in _readiness().lower()


def test_the_readiness_check_fails_when_a_page_lacks_a_published_translation(
    seeded: SeedReport,
) -> None:
    pages = _every_page_in_every_language()
    pages["withdrawal", "de"].unpublish()

    with pytest.raises(CommandError) as excinfo:
        _readiness()

    assert [(p.kind, p.language, p.missing) for p in legal_readiness()] == [
        ("withdrawal", "de", "translation")
    ]
    assert "withdrawal" in str(excinfo.value)
    assert "[de]" in str(excinfo.value)


def test_a_draft_translation_does_not_count_as_published(seeded: SeedReport) -> None:
    pages = _every_page_in_every_language()
    shipping_es = pages["shipping", "es"]
    shipping_es.unpublish()
    shipping_es.title = "Envíos (borrador)"
    shipping_es.save_revision()

    assert [(p.kind, p.language) for p in legal_readiness()] == [("shipping", "es")]


def test_the_readiness_check_fails_without_the_counsel_check_of_a_language(
    seeded: SeedReport,
) -> None:
    pages = _every_page_in_every_language()
    terms_fr = pages["terms", "fr"]
    terms_fr.body = [("paragraph", "<p>Nouvelles conditions.</p>")]
    terms_fr.effective_date = dt.date(2027, 6, 1)
    _publish(terms_fr)  # a new version: the checks of version 1 do not cover it
    _review(terms_fr, "native_review")

    assert [(p.kind, p.language, p.missing) for p in legal_readiness()] == [
        ("terms", "fr", "counsel_check")
    ]
    with pytest.raises(CommandError):
        _readiness()


# --- Seed placeholders


def test_the_seed_publishes_placeholders_of_the_five_pages_in_every_language(
    seeded: SeedReport,
) -> None:
    report = seed_site(legal_placeholders=True)

    live = LegalPage.objects.live().select_related("locale")
    published = {(page.kind, page.locale.language_code) for page in live}
    assert published == {
        (kind, code) for kind in GO_LIVE_LEGAL_KINDS for code in configured_languages()
    }
    assert len(report.created) == len(GO_LIVE_LEGAL_KINDS) * len(configured_languages())
    italian_terms = live.get(kind="terms", locale__language_code="it")
    assert italian_terms.title == "Condizioni di vendita"
    english_terms = live.get(kind="terms", locale__language_code="en")
    assert italian_terms.translation_key == english_terms.translation_key
    assert "Placeholder" in str(english_terms.body)
    assert all(page.versions.exists() for page in live)

    # Placeholders are drafts, not legal texts: the go-live check still fails on them.
    problems = legal_readiness()
    assert {p.missing for p in problems} == {"native_review", "counsel_check"}

    assert seed_site(legal_placeholders=True).created == []


def test_a_placeholder_says_it_is_not_a_legal_text(client: Client, seeded: SeedReport) -> None:
    seed_site(legal_placeholders=True)
    page = LegalPage.objects.live().get(kind="withdrawal", locale__language_code="de")

    html = client.get(page.url).content.decode()

    assert "Platzhalter" in html


# --- Per-language reviews


def test_a_review_is_recorded_on_one_language_version_and_audited(seeded: SeedReport) -> None:
    pages = _every_page_in_every_language(reviewed=False)
    version = pages["privacy", "it"].versions.get()

    record_legal_review(
        version,
        "counsel_check",
        reviewer="Studio Legale Esempio",
        reviewed_on=REVIEWED_ON,
        note="Checked against the Italian consumer code.",
        ctx=CTX,
    )

    review = LegalPageReview.objects.get()
    assert (review.version, review.stage, review.reviewer) == (
        version,
        "counsel_check",
        "Studio Legale Esempio",
    )
    assert review.reviewed_on == REVIEWED_ON
    assert review.recorded_by == "command:test"
    entry = AuditLog.objects.get(action=REVIEW_ACTION)
    after = entry.after or {}
    assert after["language_code"] == "it"
    assert after["stage"] == "counsel_check"
    assert after["reviewer"] == "Studio Legale Esempio"
    assert after["version"] == 1
    # The other languages of the same page are not covered by it.
    assert not pages["privacy", "en"].versions.get().reviews.exists()


def test_a_review_dry_run_writes_nothing(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    changes = record_legal_review(
        page.versions.get(),
        "ai_draft",
        reviewer="Translation model",
        reviewed_on=REVIEWED_ON,
        ctx=CTX,
        dry_run=True,
    )
    assert len(changes.changes) == 1
    assert not LegalPageReview.objects.exists()
    assert not AuditLog.objects.filter(action=REVIEW_ACTION).exists()


def test_a_review_cannot_be_dated_in_the_future_or_recorded_twice(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    version = page.versions.get()
    tomorrow = timezone.localdate() + dt.timedelta(days=1)
    with pytest.raises(ValidationError):
        record_legal_review(version, "counsel_check", reviewer="X", reviewed_on=tomorrow, ctx=CTX)
    with pytest.raises(ValidationError):
        record_legal_review(version, "proofreading", reviewer="X", reviewed_on=REVIEWED_ON, ctx=CTX)
    with pytest.raises(ValidationError):
        record_legal_review(
            version, "counsel_check", reviewer=" ", reviewed_on=REVIEWED_ON, ctx=CTX
        )

    record_legal_review(version, "counsel_check", reviewer="X", reviewed_on=REVIEWED_ON, ctx=CTX)
    with pytest.raises(ValidationError):
        record_legal_review(
            version, "counsel_check", reviewer="Y", reviewed_on=REVIEWED_ON, ctx=CTX
        )
    assert LegalPageReview.objects.count() == 1


def test_reviews_are_append_only(seeded: SeedReport) -> None:
    review = LegalPageReviewFactory.create()
    with pytest.raises(PermissionError):
        review.save()
    rows = LegalPageReview.objects.filter(pk=review.pk)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.update(reviewer="Someone else")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        rows.delete()


def test_the_page_is_marked_as_a_draft_until_counsel_has_checked_it(
    client: Client, seeded: SeedReport
) -> None:
    page = _publish(LegalPageFactory.create(title="Terms of sale", live=False))
    notice = "not been checked by legal counsel"
    assert notice in client.get("/terms-of-sale/").content.decode()

    _review(page, "native_review")
    assert notice in client.get("/terms-of-sale/").content.decode()

    _review(page, "counsel_check")
    assert notice not in client.get("/terms-of-sale/").content.decode()


# --- Own effective date per language, through wagtail-localize


def _italian(english: LegalPage) -> LegalPage:
    page: LegalPage = LegalPage.objects.get(
        translation_key=english.translation_key, locale__language_code="it"
    )
    return page


def _sync_enabled(page: LegalPage) -> bool:
    enabled: bool = Translation.objects.filter(
        source__object_id=page.translation_key,
        target_locale=page.locale,
        enabled=True,
    ).exists()
    return enabled


def test_a_translator_sets_the_translations_own_date_while_sync_stays_on(
    client: Client, seeded: SeedReport, editor: User
) -> None:
    english = _publish(
        LegalPageFactory.create(
            title="Terms of sale", effective_date=dt.date(2027, 1, 1), live=False
        )
    )
    translate_page(english, ["it"], user=editor)
    italian = _italian(english)

    set_legal_effective_date(italian, dt.date(2027, 3, 1), user=editor, ctx=CTX)

    italian = _italian(english)
    assert italian.effective_date == dt.date(2027, 3, 1)
    assert _sync_enabled(italian)  # the date needs no "stop sync"
    assert list(italian.versions.values_list("number", "effective_date")) == [
        (1, dt.date(2027, 1, 1)),
        (2, dt.date(2027, 3, 1)),
    ]
    entry = AuditLog.objects.get(action=DATE_ACTION)
    assert (entry.before or {})["effective_date"] == "2027-01-01"
    assert (entry.after or {})["effective_date"] == "2027-03-01"
    assert (entry.after or {})["language_code"] == "it"

    # A correction of the English text (same date) reaches Italian; Italian keeps its date.
    english.body = [("paragraph", "<p>Corrected terms.</p>")]
    english = _publish(english)
    sync_page_translations(english, user=editor)

    italian = _italian(english)
    assert italian.effective_date == dt.date(2027, 3, 1)
    assert "Corrected terms." in str(italian.body)  # the text does follow the source
    assert italian.versions.order_by("-number").values_list("effective_date", flat=True)[
        0
    ] == dt.date(2027, 3, 1)
    html = client.get(italian.url).content.decode()
    assert '<time datetime="2027-03-01">' in html


def test_a_sync_of_a_newer_source_text_does_not_backdate_the_translation(
    seeded: SeedReport, editor: User
) -> None:
    """Regression: a new English text effective next month, synced to Italian, must not become
    the Italian text in force since Italian's original date (a correction under CMS.5)."""
    today = timezone.localdate()
    first, later = today - dt.timedelta(days=300), today + dt.timedelta(days=23)
    english = _publish(
        LegalPageFactory.create(title="Terms of sale", effective_date=first, live=False)
    )
    translate_page(english, ["it"], user=editor)
    italian = _italian(english)
    assert italian.effective_date == first

    english.body = [("paragraph", "<p>Revised terms.</p>")]
    english.effective_date = later
    english = _publish(english)
    sync_page_translations(english, user=editor)

    italian = _italian(english)
    assert "Revised terms." in str(italian.body)
    stamps = list(italian.versions.order_by("number").values_list("number", "effective_date"))
    assert stamps == [(1, first), (2, later)]  # superseded from the new date, not corrected
    in_force = version_in_force([VersionStamp(n, d) for n, d in stamps], today)
    assert in_force is not None
    assert in_force.number == 1
    assert "Sample terms." in str(italian.versions.get(number=1).content()["body"])


def test_a_translators_date_is_checked_and_can_be_dry_run(seeded: SeedReport, editor: User) -> None:
    english = _publish(
        LegalPageFactory.create(
            title="Terms of sale", effective_date=dt.date(2027, 3, 1), live=False
        )
    )
    translate_page(english, ["it"], user=editor)
    italian = _italian(english)

    changes = set_legal_effective_date(
        italian, dt.date(2027, 5, 1), user=editor, ctx=CTX, dry_run=True
    )
    assert len(changes.changes) == 1
    assert _italian(english).effective_date == dt.date(2027, 3, 1)
    assert italian.versions.count() == 1
    assert not AuditLog.objects.filter(action=DATE_ACTION).exists()

    # Not before the text it translates takes effect, nor before its own last version.
    with pytest.raises(ValidationError):
        set_legal_effective_date(italian, dt.date(2027, 2, 1), user=editor, ctx=CTX)
    set_legal_effective_date(italian, dt.date(2027, 5, 1), user=editor, ctx=CTX)
    with pytest.raises(ValidationError):
        set_legal_effective_date(italian, dt.date(2027, 4, 1), user=editor, ctx=CTX)
    # The same date again changes nothing.
    assert set_legal_effective_date(italian, dt.date(2027, 5, 1), user=editor, ctx=CTX).is_empty


def test_staff_set_a_translations_date_from_the_command_line(
    seeded: SeedReport, editor: User
) -> None:
    english = _publish(LegalPageFactory.create(title="Terms of sale", live=False))
    translate_page(english, ["it"], user=editor)
    italian = _italian(english)
    out = io.StringIO()
    args = [str(italian.pk), "2027-04-01"]

    call_command("set_legal_effective_date", *args, "--dry-run", stdout=out)
    assert _italian(english).effective_date == dt.date(2027, 1, 1)

    call_command("set_legal_effective_date", *args, stdout=out)
    assert _italian(english).effective_date == dt.date(2027, 4, 1)
    entry = AuditLog.objects.get(action=DATE_ACTION)
    assert entry.actor_label == "command:set_legal_effective_date"
    assert _sync_enabled(italian)
    with pytest.raises(CommandError, match="before"):
        call_command("set_legal_effective_date", str(italian.pk), "2026-12-01", stdout=out)
    with pytest.raises(CommandError, match="No legal page"):
        call_command("set_legal_effective_date", "999999", "2027-04-01", stdout=out)


# --- No EN fallback


def test_a_legal_page_has_no_english_fallback(client: Client, seeded: SeedReport) -> None:
    _publish(LegalPageFactory.create(title="Terms of sale", live=False))

    response = client.get("/de/terms-of-sale/")

    assert response.status_code == 404
    assert "Sample terms." not in response.content.decode()
    assert LegalPage.language_fallback is False


def test_staff_record_a_review_from_the_command_line(seeded: SeedReport) -> None:
    page = _publish(LegalPageFactory.create(live=False))
    out = io.StringIO()
    args = [str(page.pk), "counsel_check", "--reviewer", "Counsel", "--date", "2026-10-01"]

    call_command("record_legal_review", *args, "--dry-run", stdout=out)
    assert not LegalPageReview.objects.exists()

    call_command("record_legal_review", *args, stdout=out)
    review = LegalPageReview.objects.get()
    assert (review.stage, review.reviewer, review.recorded_by) == (
        "counsel_check",
        "Counsel",
        "command:record_legal_review",
    )
    with pytest.raises(CommandError, match="already recorded"):
        call_command("record_legal_review", *args, stdout=out)
    with pytest.raises(CommandError, match="No published version"):
        call_command(
            "record_legal_review", "999999", "ai_draft", "--reviewer", "X", "--date", "2026-10-01"
        )
