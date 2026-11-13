"""FIT.1: the categories expected to declare fitment (an operator setting, none by default)."""

import pytest

from apps.catalog.tests.factories import CategoryFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import FitmentCategory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def test_no_category_is_relevant_until_the_operator_says_so() -> None:
    category = CategoryFactory.create()
    assert services.is_fitment_relevant(category) is False
    assert services.relevant_category_ids() == set()


def test_categories_are_set_in_bulk_with_a_dry_run_and_an_audit_entry() -> None:
    first, second, third = CategoryFactory.create_batch(3)
    changes = services.set_fitment_relevant([first, second], True, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 2
    assert not FitmentCategory.objects.exists()

    services.set_fitment_relevant([first, second], True, ctx=CTX)
    assert services.relevant_category_ids() == {first.pk, second.pk}
    assert services.is_fitment_relevant(first) is True
    assert services.is_fitment_relevant(third) is False
    assert AuditLog.objects.filter(action="fitment.category.relevance_changed").count() == 2


def test_setting_the_same_value_again_changes_and_audits_nothing() -> None:
    category = CategoryFactory.create()
    services.set_fitment_relevant([category], True, ctx=CTX)
    changes = services.set_fitment_relevant([category], True, ctx=CTX)
    assert changes.is_empty
    assert AuditLog.objects.filter(action="fitment.category.relevance_changed").count() == 1


def test_a_category_can_be_switched_off_again() -> None:
    category = CategoryFactory.create()
    services.set_fitment_relevant([category], True, ctx=CTX)
    services.set_fitment_relevant([category], False, ctx=CTX)
    assert services.is_fitment_relevant(category) is False
    assert not FitmentCategory.objects.exists()
