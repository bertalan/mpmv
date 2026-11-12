"""OP-28 (Omnibus): product reviews are verified only through a real order (database)."""

import datetime as dt

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import services
from apps.orders.models import OrderLine, ProductReview
from apps.orders.tests.factories import (
    OrderLineFactory,
    ProductReviewFactory,
)

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def bought_line(*, customer, product, paid: bool = True) -> OrderLine:  # type: ignore[no-untyped-def]
    line = OrderLineFactory.create(
        product=product,
        vendor_order__vendor=product.vendor,
        vendor_order__order__customer=customer,
    )
    if paid:
        order = line.vendor_order.order
        services.transition_order(order, "awaiting_payment", ctx=CTX)
        services.transition_order(order, "paid", ctx=CTX)
    return line


def test_a_new_review_is_not_verified_and_the_reviewer_cannot_set_it() -> None:
    review = ProductReviewFactory.create()
    assert review.verified_line is None
    assert review.is_verified is False
    assert review.badge == "not_verified"
    assert ProductReview._meta.get_field("verified_line").editable is False


def test_a_review_tied_to_a_paid_order_of_the_same_customer_becomes_verified() -> None:
    customer, product = UserFactory.create(), ProductFactory.create()
    line = bought_line(customer=customer, product=product)
    review = ProductReviewFactory.create(customer=customer, product=product)

    changes = services.verify_review(review, ctx=CTX)

    review.refresh_from_db()
    assert review.verified_line == line
    assert review.badge == "verified"
    assert changes.summary()["update"] == 1
    assert AuditLog.objects.filter(action="orders.review_verify").count() == 1


def test_an_unpaid_order_or_another_customer_leaves_the_review_not_verified() -> None:
    customer, product = UserFactory.create(), ProductFactory.create()
    bought_line(customer=customer, product=product, paid=False)
    bought_line(customer=UserFactory.create(), product=product)
    review = ProductReviewFactory.create(customer=customer, product=product)

    services.verify_review(review, ctx=CTX)

    review.refresh_from_db()
    assert review.is_verified is False
    assert not AuditLog.objects.filter(action="orders.review_verify").exists()


def test_a_migrated_legacy_review_stays_not_verified_without_a_matching_order() -> None:
    review = ProductReviewFactory.create(
        origin=ProductReview.Origin.LEGACY_IMPORT, customer=None, display_name="Marco"
    )
    services.verify_review(review, ctx=CTX)
    review.refresh_from_db()
    assert review.badge == "not_verified"
    assert review.visible is True


def test_a_legacy_review_with_a_matching_order_of_the_same_customer_is_verified() -> None:
    customer, product = UserFactory.create(), ProductFactory.create()
    bought_line(customer=customer, product=product)
    review = ProductReviewFactory.create(
        origin=ProductReview.Origin.LEGACY_IMPORT, customer=customer, product=product
    )
    services.verify_review(review, ctx=CTX)
    review.refresh_from_db()
    assert review.badge == "verified"


def test_a_dry_run_reports_the_change_and_writes_nothing() -> None:
    customer, product = UserFactory.create(), ProductFactory.create()
    bought_line(customer=customer, product=product)
    review = ProductReviewFactory.create(customer=customer, product=product)

    changes = services.verify_review(review, ctx=CTX, dry_run=True)

    assert changes.summary()["update"] == 1
    review.refresh_from_db()
    assert review.verified_line is None
    assert not AuditLog.objects.filter(action="orders.review_verify").exists()


def test_the_verification_is_dropped_when_the_order_is_refunded() -> None:
    customer, product = UserFactory.create(), ProductFactory.create()
    line = bought_line(customer=customer, product=product)
    review = ProductReviewFactory.create(customer=customer, product=product)
    services.verify_review(review, ctx=CTX)
    order = line.vendor_order.order
    services.transition_order(order, "partially_refunded", ctx=CTX)

    services.verify_review(review, ctx=CTX)

    review.refresh_from_db()
    assert review.badge == "not_verified"


def test_the_rating_is_between_one_and_five() -> None:
    for bad in (0, 6):
        with pytest.raises(IntegrityError), transaction.atomic():
            ProductReviewFactory.create(rating=bad)


def test_the_review_date_is_the_original_one() -> None:
    when = dt.datetime(2022, 3, 4, tzinfo=dt.UTC)
    review = ProductReviewFactory.create(reviewed_at=when)
    review.refresh_from_db()
    assert review.reviewed_at == when
