"""CMS.13: editor-built form pages on Wagtail's form builder.

Done when: "A form page published by an editor refuses a submission without a valid Turnstile
token, appears in the SEC.13 register without a manual step, and its submissions are deleted at
the end of their retention period".
"""

import datetime as dt
from collections.abc import Iterator
from html import unescape
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone
from django.utils.translation import gettext
from wagtail.contrib.forms.models import FormSubmission

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.accounts.tests.mfa import verified_login
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
        assert gettext("The security check failed. Please try again.") in unescape(
            response.content.decode()
        )
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


# --- Review round: rate limit, size limits, hostile content, who sees the submissions

HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def test_a_client_over_the_rate_limit_gets_429_and_nothing_is_stored(
    client: Client, seeded: SeedReport, settings: Any
) -> None:
    settings.FORM_PAGE_RATE_LIMIT = 2
    page = a_form(seeded)
    for _ in range(2):
        assert submit(client, page, FakeCaptchaVerifier.VALID_TOKEN).status_code == 200
    response = submit(client, page, FakeCaptchaVerifier.VALID_TOKEN)
    assert response.status_code == 429
    assert int(response["Retry-After"]) >= 1
    assert FormSubmission.objects.count() == 2
    assert client.get(page.url).status_code == 200  # reading is not limited


def test_the_rate_limit_is_per_client_address(
    client: Client, seeded: SeedReport, settings: Any
) -> None:
    settings.FORM_PAGE_RATE_LIMIT = 1
    page = a_form(seeded)
    assert submit(client, page, FakeCaptchaVerifier.VALID_TOKEN).status_code == 200
    assert submit(client, page, FakeCaptchaVerifier.VALID_TOKEN).status_code == 429
    other = Client(REMOTE_ADDR="203.0.113.77")
    assert submit(other, page, FakeCaptchaVerifier.VALID_TOKEN).status_code == 200


def test_an_oversize_value_is_refused_per_field_and_not_stored(
    client: Client, seeded: SeedReport
) -> None:
    from apps.cms.forms_pages import FIELD_MAX_LENGTH

    page = a_form(seeded)  # a multiline field
    too_long = "a" * (FIELD_MAX_LENGTH["multiline"] + 1)
    response = submit(client, page, FakeCaptchaVerifier.VALID_TOKEN, too_long)
    assert response.status_code == 200  # shown again with its error
    assert not FormSubmission.objects.exists()
    ok = submit(client, page, FakeCaptchaVerifier.VALID_TOKEN, "a" * FIELD_MAX_LENGTH["multiline"])
    assert ok.status_code == 200
    assert FormSubmission.objects.count() == 1


@pytest.mark.parametrize("field_type", ["singleline", "email", "url"])
def test_every_text_field_type_has_a_max_length(field_type: str, seeded: SeedReport) -> None:
    from apps.cms.forms_pages import FIELD_MAX_LENGTH

    page = FormPageFactory.create(title="Types")
    FormFieldFactory.create(page=page, label="Thing", field_type=field_type)
    form = page.get_form()
    assert form.fields["thing"].max_length == FIELD_MAX_LENGTH[field_type]


def test_an_oversize_submission_is_refused_with_400_and_not_stored(
    client: Client, seeded: SeedReport, settings: Any
) -> None:
    settings.FORM_PAGE_MAX_SUBMISSION_BYTES = 1024
    page = a_form(seeded)
    response = client.post(
        page.url,
        {
            "your_question": "ok",
            "padding": "x" * 2000,
            TOKEN_FIELD: FakeCaptchaVerifier.VALID_TOKEN,
        },
    )
    assert response.status_code == 400
    assert not FormSubmission.objects.exists()


def test_hostile_labels_and_values_are_escaped_everywhere(
    client: Client, seeded: SeedReport
) -> None:
    page = FormPageFactory.create(title="Hostile")
    FormFieldFactory.create(page=page, label=HOSTILE, field_type="multiline", required=True)
    page.save_revision().publish()
    page = FormPage.objects.get(pk=page.pk)
    name = page.form_fields.get().clean_name

    html = client.get(page.url).content.decode()
    assert "<script>alert" not in html
    assert "<img src=x" not in html

    # The value comes back escaped in the redisplayed form (invalid: too long) and the landing page.
    sent = client.post(page.url, {name: HOSTILE, TOKEN_FIELD: FakeCaptchaVerifier.VALID_TOKEN})
    assert sent.status_code == 200
    assert "<script>alert" not in sent.content.decode()
    assert "<img src=x" not in sent.content.decode()
    assert FormSubmission.objects.get().get_data()[name] == HOSTILE  # stored as typed

    # The admin list of submissions escapes both the label (column) and the value.
    from django.urls import reverse

    verified_login(client, UserFactory.create(superuser=True))
    listing = client.get(reverse("wagtailforms:list_submissions", args=(page.pk,)))
    assert listing.status_code == 200
    body = listing.content.decode()
    assert "<script>alert" not in body
    assert "<img src=x" not in body
    assert "&lt;script&gt;" in body


def test_a_user_without_page_rights_cannot_see_the_submissions(
    client: Client, seeded: SeedReport
) -> None:
    from django.urls import reverse

    page = a_form(seeded)
    FormSubmission.objects.create(page=page, form_data={"your_question": "private words"})
    url = reverse("wagtailforms:list_submissions", args=(page.pk,))

    anonymous = client.get(url)
    assert anonymous.status_code in (302, 403, 404)
    assert "private words" not in anonymous.content.decode()

    verified_login(client, UserFactory.create())  # no group, no page rights
    plain = client.get(url)
    assert plain.status_code in (302, 403, 404)
    assert "private words" not in plain.content.decode()
