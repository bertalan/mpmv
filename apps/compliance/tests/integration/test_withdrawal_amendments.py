"""ORD.20 (2/3) and CMP.6: a withdrawal closes the package's open amendment as `withdrawn`, and a
package with a registered withdrawal can no longer be amended (spec 07, Withdrawal in the
meantime). The amendment side is `apps.orders.amendments`; orders reaches the statements only
through the `ORDER_WITHDRAWAL_HOOKS` setting."""

from collections.abc import Iterator

import pytest
from django.utils import translation

from apps.compliance import withdrawal
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import amendment_engine as am
from apps.orders import amendments, versions
from apps.orders.models import AmendmentState, OrderAmendment
from apps.orders.tests.control import plainly
from apps.orders.tests.factories import OrderAmendmentFactory
from apps.orders.tests.integration.test_amendments import (  # noqa: F401 (autouse fixture)
    draft,
    key_of,
    seed,
    tax_setup,
)

pytestmark = pytest.mark.django_db

BUYER = AuditContext(actor_label="anonymous", source=AuditLog.Source.SYSTEM)


@pytest.fixture(autouse=True)
def _english() -> Iterator[None]:
    translation.activate("en")
    yield
    translation.activate("en")


def test_a_withdrawal_closes_the_open_amendment_and_blocks_new_drafts() -> None:
    sub = seed()
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    withdrawal.submit_withdrawal(sub.order, [sub.public_id], ctx=BUYER)
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.WITHDRAWN, plainly(
        "a withdrawal registered while an amendment is open must close it as withdrawn",
        f"the amendment is {amendment.state}",
    )
    assert withdrawal.has_withdrawal(sub)
    with pytest.raises(amendments.AmendmentRefused, match="withdrawn"):
        draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert OrderAmendment.objects.filter(vendor_order=sub).count() == 1


def test_a_withdrawal_dry_run_leaves_the_amendment_open() -> None:
    sub = seed()
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    withdrawal.submit_withdrawal(sub.order, [sub.public_id], ctx=BUYER, dry_run=True)
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.DRAFT
    assert not withdrawal.has_withdrawal(sub)


def test_the_withdrawal_lists_the_lines_of_the_current_version() -> None:
    sub = seed()
    v1 = am.VersionSnapshot.from_json(sub.versions.get(number=1).snapshot)
    without_b = am.VersionSnapshot(
        lines=tuple(line for line in v1.lines if line.sku != "SKU-B"),
        shipping=v1.shipping,
        commission=v1.commission,
        destination_country=v1.destination_country,
    )
    record = OrderAmendmentFactory.create(vendor_order=sub, from_version=sub.versions.get(number=1))
    versions.create_next_version(sub, without_b, number=2, amendment=record, shipping_address=None)
    sub.refresh_from_db()
    titles = [line["title"] for line in withdrawal._lines(sub)]
    assert titles == ["A"], plainly(
        "after B was removed, the withdrawal acknowledgement must list only A",
        f"it lists {titles}",
    )
