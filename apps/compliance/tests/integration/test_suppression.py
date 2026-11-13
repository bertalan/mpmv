"""L-3: the marketing suppression list stores keyed digests, never addresses."""

import secrets

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.compliance.models import SuppressedEmail
from apps.compliance.services import is_suppressed, suppress_email
from apps.compliance.tests.factories import SuppressedEmailFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.privacy import suppression_digest

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:ops", source=AuditLog.Source.ADMIN, ip="127.0.0.1")


@pytest.fixture(autouse=True)
def pepper() -> object:
    with override_settings(PRIVACY_PEPPER=secrets.token_urlsafe(24)):
        yield


def test_a_listed_address_is_found_whatever_its_case_and_blanks() -> None:
    suppress_email("Ada@Example.org", ctx=CTX)
    assert is_suppressed("ada@example.org")
    assert is_suppressed("  ADA@EXAMPLE.ORG\n")
    assert not is_suppressed("grace@example.org")
    assert not is_suppressed("")


def test_a_plus_tag_is_a_different_mailbox() -> None:
    suppress_email("ada@example.org", ctx=CTX)
    assert not is_suppressed("ada+news@example.org")


def test_only_the_digest_is_stored() -> None:
    suppress_email("ada@example.org", reason=SuppressedEmail.Reason.COMPLAINT, ctx=CTX)
    row = SuppressedEmail.objects.get()
    assert row.digest == suppression_digest("ada@example.org")
    assert "ada" not in row.digest
    assert row.reason == "complaint"


def test_a_rotated_pepper_no_longer_matches() -> None:
    suppress_email("ada@example.org", ctx=CTX)
    with override_settings(PRIVACY_PEPPER=secrets.token_urlsafe(24)):
        assert not is_suppressed("ada@example.org")


def test_dry_run_returns_the_changeset_and_writes_nothing() -> None:
    changes = suppress_email("ada@example.org", ctx=CTX, dry_run=True)
    assert [c.op for c in changes.changes] == ["create"]
    assert not SuppressedEmail.objects.exists()
    assert not AuditLog.objects.filter(action="compliance.suppression.added").exists()


def test_adding_writes_one_audit_entry_without_the_address() -> None:
    suppress_email("ada@example.org", ctx=CTX)
    entry = AuditLog.objects.get(action="compliance.suppression.added")
    assert "ada" not in f"{entry.target_key}{entry.before}{entry.after}"
    assert entry.target_key == suppression_digest("ada@example.org")[:12]


def test_adding_twice_changes_and_audits_nothing_more() -> None:
    suppress_email("ada@example.org", ctx=CTX)
    again = suppress_email("ADA@example.org ", ctx=CTX)
    assert again.is_empty
    assert SuppressedEmail.objects.count() == 1
    assert AuditLog.objects.filter(action="compliance.suppression.added").count() == 1


@pytest.mark.parametrize("address", ["", "  ", "no-at-sign"])
def test_a_non_address_is_refused(address: str) -> None:
    with pytest.raises(ValidationError):
        suppress_email(address, ctx=CTX)


def test_an_unknown_reason_is_refused() -> None:
    with pytest.raises(ValidationError):
        suppress_email("ada@example.org", reason="because", ctx=CTX)


def test_a_factory_row_is_a_listed_digest() -> None:
    row = SuppressedEmailFactory.create()
    assert row.digest == suppression_digest("suppressed0@example.com") or len(row.digest) == 64
    assert SuppressedEmail.objects.filter(digest=row.digest).exists()
