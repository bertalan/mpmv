"""EVT.1 done-when: vendor A cannot see vendor B's draft page (cross-tenant fixture, CORE.3).

`EventPage.objects` is Wagtail's page manager with the CORE.3 scoping: staff see every event,
a vendor member only its own vendor's, everyone else none. Platform events (no vendor) are
staff-only. The generated tests of `tests.integration.test_tenant_isolation` cover the same
through `tests.tenancy_registry.OWNED_OBJECTS`.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404

from apps.accounts.tests.factories import UserFactory
from apps.events.models import EventPage, VendorEventPolicy
from apps.events.tests.factories import EventPageFactory, VendorEventPolicyFactory
from apps.vendors.scoping import get_for_actor_or_404, get_vendor_object_or_404
from tests.tenancy import CrossTenant

pytestmark = pytest.mark.django_db


def test_vendor_a_cannot_see_vendor_bs_draft_page(cross_tenant: CrossTenant) -> None:
    a, b = cross_tenant.a, cross_tenant.b
    mine = EventPageFactory.create(vendor=a.vendor)
    theirs = EventPageFactory.create(vendor=b.vendor)
    assert not theirs.live

    visible = EventPage.objects.for_member(a.owner)
    assert mine in visible
    assert theirs not in visible
    assert theirs not in EventPage.objects.for_actor(a.owner)
    assert get_vendor_object_or_404(EventPage, a.owner, pk=mine.pk) == mine
    with pytest.raises(Http404):
        get_vendor_object_or_404(EventPage, a.owner, pk=theirs.pk)
    with pytest.raises(Http404):
        get_for_actor_or_404(EventPage.objects.child_of(theirs.get_parent()), a.owner, pk=theirs.pk)


def test_scoping_keeps_the_page_queryset(cross_tenant: CrossTenant) -> None:
    a = cross_tenant.a
    draft = EventPageFactory.create(vendor=a.vendor)
    live = EventPageFactory.create(vendor=a.vendor, live=True)
    assert list(EventPage.objects.live().for_member(a.owner)) == [live]
    assert draft in EventPage.objects.not_live().for_member(a.owner)


def test_platform_events_are_for_staff_only(cross_tenant: CrossTenant) -> None:
    platform = EventPageFactory.create(platform=True)
    staff = UserFactory.create(platform_staff=True)
    assert platform in EventPage.objects.for_actor(staff)
    assert platform not in EventPage.objects.for_member(cross_tenant.a.owner)
    assert not EventPage.objects.for_actor(AnonymousUser()).exists()


def test_vendor_a_cannot_see_vendor_bs_policy(cross_tenant: CrossTenant) -> None:
    a, b = cross_tenant.a, cross_tenant.b
    theirs = VendorEventPolicyFactory.create(vendor=b.vendor)
    assert theirs not in VendorEventPolicy.objects.for_member(a.owner)
    with pytest.raises(Http404):
        get_vendor_object_or_404(VendorEventPolicy, a.owner, pk=theirs.pk)
