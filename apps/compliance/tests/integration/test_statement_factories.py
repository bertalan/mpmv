"""CMP.5 part 3: the statement, restriction and transparency factories build valid, linked rows."""

import pytest

from apps.compliance.tests.factories import (
    ProductRestrictionFactory,
    StatementOfReasonsFactory,
    TransparencySubmissionFactory,
)

pytestmark = pytest.mark.django_db


def test_a_statement_factory_builds_a_sent_statement() -> None:
    statement = StatementOfReasonsFactory.create()
    assert statement.sent_at is not None
    assert statement.vendor_id is not None


def test_a_transparency_submission_is_linked_to_its_statement() -> None:
    submission = TransparencySubmissionFactory.create()
    assert submission.statement.transparency == submission


def test_a_product_restriction_holds_its_product_under_a_statement() -> None:
    restriction = ProductRestrictionFactory.create()
    assert restriction.statement.pk is not None
    assert restriction.product.pk is not None
