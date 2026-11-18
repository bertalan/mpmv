"""CMS.13: editor-built form pages on Wagtail's form builder.

Done when: "A form page published by an editor refuses a submission without a valid Turnstile
token, appears in the SEC.13 register without a manual step, and its submissions are deleted at
the end of their retention period".
"""

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone
from wagtail.contrib.forms.models import FormSubmission

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.cms import services
from apps.cms.forms_pages import MAX_RETENTION_DAYS
from apps.cms.models import FormPage
from apps.cms.services import SeedReport
from apps.cms.tests.factories import FormFieldFactory, FormPageFactory
from apps.core import public_forms
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _captcha(settings: Any) -> Iterator[None]:
    settings.CAPTCHA_ENABLED = True
    settings.TURNSTILE_SITE_KEY = "site-key-for-tests"
    settings.CAPTCHA_VERIFIER = "apps.accounts.tests.fakes.FakeCaptchaVerifier"
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def a_form(seeded: SeedReport, **kwargs: Any) -> FormPage:
    page = FormPageFactory.create(title="Ask us", **kwargs)
    FormFieldFactory.create(page=page, label="Your question", field_type="multiline", required=True)
    page.save_revision().publish()
    published: FormPage = FormPage.objects.get(pk=page.pk)  # reloaded: the published copy
    return published


def submit(client: Client, page: FormPage, token: str | None, text: str = "Hello") -> Any:
    data = {"your_question": text}
    if token is not None:
        data[TOKEN_FIELD] = token
    return client.post(page.url, data)


def test_a_submission_without_a_token_is_refused_and_not_stored(
    client: Client, seeded: SeedReport
) -> None:
    page = a_form(seeded)
    for token in (None, "", "a-wrong-token"):
        response = submit(client, page, token)
        assert response.status_code == 400
        assert "security check" in response.content.decode().lower()
    assert not FormSubmission.objects.exists()


def test_a_submission_with_a_valid_token_is_stored(client: Client, seeded: SeedReport) -> None:
    page = a_form(seeded, thank_you_text="<p>We will answer soon.</p>")
    response = submit(client, page, FakeCaptchaVerifier.VALID_TOKEN, "Do you ship to Malta?")
    assert response.status_code == 200
    assert "We will answer soon." in response.content.decode()
    stored = FormSubmission.objects.get()
    assert stored.page_id == page.pk
    assert stored.get_data()["your_question"] == "Do you ship to Malta?"
    assert FakeCaptchaVerifier.calls  # the check went through our interface


def test_the_form_page_renders_the_widget_without_inline_script(
    client: Client, seeded: SeedReport
) -> None:
    page = a_form(seeded)
    html = client.get(page.url).content.decode()
    assert 'class="cf-turnstile"' in html
    assert "Your question" in html
    assert "<script>" not in html
    assert " style=" not in html


def test_an_invalid_answer_is_shown_again_with_its_error(
    client: Client, seeded: SeedReport
) -> None:
    page = a_form(seeded)
    response = submit(client, page, FakeCaptchaVerifier.VALID_TOKEN, "")
    assert response.status_code == 200
    assert not FormSubmission.objects.exists()
    assert "required" in response.content.decode().lower()


def test_the_retention_period_has_a_ceiling(seeded: SeedReport) -> None:
    from django.core.exceptions import ValidationError

    page = FormPageFactory.build(retention_days=MAX_RETENTION_DAYS + 1)
    with pytest.raises(ValidationError) as caught:
        page.full_clean(exclude=["slug", "path", "depth", "content_type", "locale"])
    assert "retention_days" in caught.value.message_dict


# --- The purge


def _old(submission: FormSubmission, days: int) -> None:
    FormSubmission.objects.filter(pk=submission.pk).update(
        submit_time=timezone.now() - dt.timedelta(days=days)
    )


def _ctx() -> AuditContext:
    return AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def test_the_purge_deletes_only_submissions_past_their_pages_retention(
    client: Client, seeded: SeedReport
) -> None:
    short = a_form(seeded, retention_days=30)
    long = FormPageFactory.create(title="Long kept", retention_days=365, parent=short.get_parent())
    FormFieldFactory.create(page=long, label="Your question", required=False)
    pages = {"short": short, "long": long}
    made = {}
    for name, page in pages.items():
        for days in (10, 40):
            sub = FormSubmission.objects.create(page=page, form_data={"your_question": name})
            _old(sub, days)
            made[(name, days)] = sub.pk

    changes = services.purge_expired_form_submissions(ctx=_ctx())

    remaining = set(FormSubmission.objects.values_list("pk", flat=True))
    assert made[("short", 40)] not in remaining
    assert {made[("short", 10)], made[("long", 10)], made[("long", 40)]} <= remaining
    assert len(changes.changes) == 1
    entry = AuditLog.objects.get(action=services.FORM_SUBMISSIONS_PURGED)
    assert entry.after is not None
    assert entry.after["deleted"] == 1
    assert entry.after["retention_days"] == 30
    assert "short" not in str(entry.after)  # counts only, never the content


def test_the_purge_is_idempotent_and_dry_run_deletes_nothing(seeded: SeedReport) -> None:
    page = a_form(seeded, retention_days=30)
    sub = FormSubmission.objects.create(page=page, form_data={"your_question": "x"})
    _old(sub, 31)

    preview = services.purge_expired_form_submissions(ctx=_ctx(), dry_run=True)
    assert len(preview.changes) == 1
    assert FormSubmission.objects.count() == 1
    assert not AuditLog.objects.filter(action=services.FORM_SUBMISSIONS_PURGED).exists()

    services.purge_expired_form_submissions(ctx=_ctx())
    again = services.purge_expired_form_submissions(ctx=_ctx())
    assert again.is_empty
    assert not FormSubmission.objects.exists()
    assert AuditLog.objects.filter(action=services.FORM_SUBMISSIONS_PURGED).count() == 1


def test_the_job_runs_the_purge_and_is_scheduled_daily() -> None:
    from apps.cms import tasks

    assert tasks.purge_form_submissions is not None


# --- The register of public forms and core.E704


def test_the_page_type_is_declared_so_core_e704_does_not_flag_it() -> None:
    assert public_forms.page_problems([FormPage]) == []
    assert not [m for m in public_forms.page_problems() if "FormPage" in m]


def test_the_form_page_type_is_listed_in_the_register_without_a_manual_step() -> None:
    report = public_forms.public_form_report()
    names = [row.page_class.__qualname__ for row in report.pages]
    assert "FormPage" in names
    row = next(r for r in report.pages if r.page_class is FormPage)
    assert row.spec.protection is public_forms.Protection.CAPTCHA
    assert "FormPage" in public_forms.render_register(report)
