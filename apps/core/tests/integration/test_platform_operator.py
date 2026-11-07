"""ARC.7: PlatformOperator as versioned configuration, never a constant (spec 01, 04, 05, 11)."""

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.models import AuditLog, PlatformOperator
from apps.core.services import (
    NoPlatformOperator,
    configure_platform_operator,
    current_operator,
)
from apps.core.tests.factories import PlatformOperatorFactory, utc

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")


def test_operator_holds_country_vat_regime_einvoice_channel_and_dac7_authority() -> None:
    op = PlatformOperatorFactory.build(
        country="it",
        vat_number="IT00000000000",
        tax_regime=PlatformOperator.TaxRegime.SMALL_BUSINESS,
        commission_vat_mode=PlatformOperator.CommissionVatMode.NONE,
        einvoice_channel=PlatformOperator.EInvoiceChannel.SDI,
        dac7_authority_country="it",
    )
    op.full_clean()
    op.save()
    stored = PlatformOperator.objects.get(pk=op.pk)
    assert stored.country == "IT"
    assert stored.dac7_authority_country == "IT"
    assert stored.commission_vat_mode == "none"
    assert stored.einvoice_channel == "sdi"
    assert stored.public_id is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("country", "XX"),
        ("dac7_authority_country", "ZZ"),
        ("commission_vat_mode", "sometimes"),
        ("einvoice_channel", "fax"),
        ("tax_regime", "unknown"),
    ],
)
def test_invalid_configuration_values_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PlatformOperatorFactory.build(**{field: value}).full_clean()
    assert field in exc.value.message_dict


def test_current_operator_returns_the_row_valid_at_the_given_instant() -> None:
    PlatformOperatorFactory.create(legal_name="Old", valid_from=utc(2027), valid_to=utc(2028))
    PlatformOperatorFactory.create(
        legal_name="New", country="DE", valid_from=utc(2028), open_ended=True
    )
    assert current_operator(utc(2027, 6)).legal_name == "Old"
    assert current_operator(utc(2028)).legal_name == "New"
    assert current_operator(utc(2030)).country == "DE"


def test_current_operator_defaults_to_now() -> None:
    op = PlatformOperatorFactory.create(valid_from=timezone.now() - dt.timedelta(days=1))
    assert current_operator() == op


def test_current_operator_without_configuration_is_a_clear_error() -> None:
    with pytest.raises(NoPlatformOperator, match="No PlatformOperator is valid at"):
        current_operator(utc(2027))


def test_current_operator_before_the_first_row_starts_is_a_clear_error() -> None:
    PlatformOperatorFactory.create(valid_from=utc(2028), open_ended=True)
    with pytest.raises(NoPlatformOperator):
        current_operator(utc(2027, 6))


def test_overlapping_validity_periods_are_refused_by_the_database() -> None:
    PlatformOperatorFactory.create(valid_from=utc(2027), open_ended=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        PlatformOperatorFactory.create(valid_from=utc(2028))


def test_adjacent_validity_periods_are_allowed() -> None:
    PlatformOperatorFactory.create(valid_from=utc(2027), valid_to=utc(2028))
    PlatformOperatorFactory.create(valid_from=utc(2028), open_ended=True)
    assert PlatformOperator.objects.count() == 2


def test_valid_to_must_follow_valid_from() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        PlatformOperatorFactory.create(valid_from=utc(2028), valid_to=utc(2027))


def test_configuration_rows_are_never_edited_in_place() -> None:
    op = PlatformOperatorFactory.create()
    op.vat_number = "ATU99999999"
    with pytest.raises(PermissionError, match="new row"):
        op.save()


def test_first_configuration_creates_an_audited_row() -> None:
    changeset = configure_platform_operator(PlatformOperatorFactory.build(country="at"), ctx=CTX)
    stored = PlatformOperator.objects.get()
    assert stored.country == "AT"
    assert stored.valid_to is None
    assert changeset.summary() == {"create": 1, "update": 0, "delete": 0}
    entry = AuditLog.objects.get()
    assert entry.action == "core.platform_operator.configured"
    assert entry.target_model == "core.platformoperator"
    assert entry.after is not None
    assert entry.after["country"] == "AT"


def test_reconfiguration_closes_the_current_row_and_opens_a_new_one() -> None:
    configure_platform_operator(PlatformOperatorFactory.build(valid_from=utc(2027)), ctx=CTX)
    relocated = PlatformOperatorFactory.build(
        legal_name="Example Marketplace GmbH",
        country="DE",
        vat_number="DE000000000",
        commission_vat_mode=PlatformOperator.CommissionVatMode.STANDARD,
        dac7_authority_country="DE",
        valid_from=utc(2029),
    )
    changeset = configure_platform_operator(relocated, ctx=CTX)

    assert changeset.summary() == {"create": 1, "update": 1, "delete": 0}
    old, new = PlatformOperator.objects.order_by("valid_from")
    assert old.valid_to == utc(2029)
    assert old.country == "AT"
    assert new.valid_to is None
    assert current_operator(utc(2028)).country == "AT"
    assert current_operator(utc(2029)).commission_vat_mode == "standard"
    assert AuditLog.objects.count() == 3  # first create + (close, create)


def test_configuration_dry_run_writes_nothing() -> None:
    changeset = configure_platform_operator(PlatformOperatorFactory.build(), ctx=CTX, dry_run=True)
    assert changeset.summary()["create"] == 1
    assert PlatformOperator.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_new_configuration_must_start_after_the_current_one() -> None:
    configure_platform_operator(PlatformOperatorFactory.build(valid_from=utc(2028)), ctx=CTX)
    with pytest.raises(ValidationError) as exc:
        configure_platform_operator(PlatformOperatorFactory.build(valid_from=utc(2028)), ctx=CTX)
    assert "valid_from" in exc.value.message_dict
    assert PlatformOperator.objects.count() == 1


def test_new_configuration_never_shortens_an_already_closed_row() -> None:
    PlatformOperatorFactory.create(valid_from=utc(2027), valid_to=utc(2029))
    with pytest.raises(ValidationError) as exc:
        configure_platform_operator(PlatformOperatorFactory.build(valid_from=utc(2028)), ctx=CTX)
    assert "valid_from" in exc.value.message_dict
    assert PlatformOperator.objects.get().valid_to == utc(2029)


def test_saved_rows_cannot_be_reconfigured() -> None:
    op = PlatformOperatorFactory.create()
    with pytest.raises(ValueError, match="unsaved"):
        configure_platform_operator(op, ctx=CTX)


def test_new_configuration_is_validated_before_writing() -> None:
    with pytest.raises(ValidationError):
        configure_platform_operator(PlatformOperatorFactory.build(country="XX"), ctx=CTX)
    assert PlatformOperator.objects.count() == 0
