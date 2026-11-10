"""CORE.3: tenant isolation. `VendorScopedQuerySet` and the 404 helpers (spec 02 §2.2).

A vendor member reaches only their own vendor's rows; anybody else gets 404, never 403, so the
answer does not tell whether another vendor's object exists.
"""

import uuid
from dataclasses import dataclass

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404

from apps.accounts.tests.factories import UserFactory
from apps.vendors.models import Vendor, VendorMember, VendorTranslation
from apps.vendors.scoping import (
    VendorScopedQuerySet,
    get_for_actor_or_404,
    get_vendor_object_or_404,
    is_vendor_scoped,
    scope_target,
)
from apps.vendors.tests.factories import (
    VendorFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)

pytestmark = pytest.mark.django_db

STAFF = VendorMember.Role.STAFF


@pytest.fixture
def two_vendors() -> tuple[VendorMember, VendorMember]:
    """The owners of Vendor A and Vendor B, each vendor with an English translation."""
    owner_a = VendorMemberFactory.create(vendor=VendorFactory.create(languages=["en"]))
    owner_b = VendorMemberFactory.create(vendor=VendorFactory.create(languages=["en"]))
    return owner_a, owner_b


# --- The scoped manager on every vendor-owned model


@pytest.mark.parametrize("model", [Vendor, VendorMember, VendorTranslation])
def test_vendor_owned_models_use_the_scoped_queryset(model: type[Vendor]) -> None:
    assert is_vendor_scoped(model)
    assert isinstance(model.objects.all(), VendorScopedQuerySet)
    assert scope_target(model) is Vendor


def test_related_managers_are_scoped_too(two_vendors: tuple[VendorMember, VendorMember]) -> None:
    owner_a, owner_b = two_vendors
    assert list(owner_a.vendor.translations.for_member(owner_b.user)) == []
    assert list(owner_a.vendor.translations.for_member(owner_a.user)) == [
        owner_a.vendor.translations.get()
    ]


def test_models_without_a_vendor_are_not_scoped() -> None:
    from apps.accounts.models import User

    assert not is_vendor_scoped(User)


# --- for_vendor


def test_for_vendor_keeps_only_that_vendors_rows(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    staff_a = VendorMemberFactory.create(vendor=owner_a.vendor, role=STAFF)

    assert list(Vendor.objects.for_vendor(owner_a.vendor)) == [owner_a.vendor]
    assert set(VendorMember.objects.for_vendor(owner_a.vendor)) == {owner_a, staff_a}
    assert list(VendorTranslation.objects.for_vendor(owner_b.vendor)) == [
        owner_b.vendor.translations.get()
    ]


# --- for_member


def test_for_member_returns_the_rows_of_the_users_vendors_only(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    assert list(Vendor.objects.for_member(owner_a.user)) == [owner_a.vendor]
    assert list(VendorMember.objects.for_member(owner_a.user)) == [owner_a]
    assert list(VendorTranslation.objects.for_member(owner_b.user)) == [
        owner_b.vendor.translations.get()
    ]


def test_for_member_counts_every_role_and_every_membership(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    third = VendorFactory.create()
    VendorMemberFactory.create(vendor=owner_b.vendor, user=owner_a.user, role=STAFF)

    assert set(Vendor.objects.for_member(owner_a.user)) == {owner_a.vendor, owner_b.vendor}
    assert third not in Vendor.objects.for_member(owner_a.user)


def test_for_member_gives_nothing_to_non_members_inactive_and_anonymous_users(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, _ = two_vendors
    stranger = UserFactory.create()
    owner_a.user.is_active = False
    owner_a.user.save(update_fields=["is_active"])

    assert not Vendor.objects.for_member(stranger).exists()
    assert not Vendor.objects.for_member(owner_a.user).exists()
    assert not Vendor.objects.for_member(AnonymousUser()).exists()


def test_platform_staff_are_not_members(two_vendors: tuple[VendorMember, VendorMember]) -> None:
    staff = UserFactory.create(staff=True)
    assert not VendorMember.objects.for_member(staff).exists()


def test_scoped_querysets_chain(two_vendors: tuple[VendorMember, VendorMember]) -> None:
    owner_a, _ = two_vendors
    staff_a = VendorMemberFactory.create(vendor=owner_a.vendor, role=STAFF)
    scoped = VendorMember.objects.filter(role=STAFF).for_member(owner_a.user)
    assert list(scoped) == [staff_a]
    assert list(scoped.for_vendor(owner_a.vendor)) == [staff_a]


# --- for_actor (spec name): staff everything, members their own, others nothing


def test_for_actor_gives_platform_staff_every_vendor(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    staff = UserFactory.create(platform_staff=True)
    assert set(Vendor.objects.for_actor(staff)) == {owner_a.vendor, owner_b.vendor}
    assert list(Vendor.objects.for_actor(owner_a.user)) == [owner_a.vendor]
    assert not Vendor.objects.for_actor(AnonymousUser()).exists()


def test_for_actor_gives_inactive_staff_nothing(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    staff = UserFactory.create(platform_staff=True, is_active=False)
    assert not Vendor.objects.for_actor(staff).exists()


# --- for_actor with a machine credential (CORE.13): API keys bound to a vendor, or platform-wide


@dataclass(frozen=True)
class StubCredential:
    """What `for_actor` needs from a credential such as `ApiClient` (vendors cannot import api)."""

    credential_vendor_id: int | None


def test_a_vendor_credential_reaches_only_its_vendors_rows(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    key_a = StubCredential(owner_a.vendor.pk)
    assert list(Vendor.objects.for_actor(key_a)) == [owner_a.vendor]
    assert list(VendorMember.objects.for_actor(key_a)) == [owner_a]
    assert list(VendorTranslation.objects.for_actor(key_a)) == [owner_a.vendor.translations.get()]
    assert owner_b not in VendorMember.objects.for_actor(key_a)


def test_a_platform_credential_reaches_every_vendor(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    assert set(Vendor.objects.for_actor(StubCredential(None))) == {owner_a.vendor, owner_b.vendor}


def test_a_vendor_credential_gets_404_for_another_vendors_object(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    key_a = StubCredential(owner_a.vendor.pk)
    assert get_for_actor_or_404(VendorMember, key_a, public_id=owner_a.public_id) == owner_a
    with pytest.raises(Http404) as theirs:
        get_for_actor_or_404(VendorMember, key_a, public_id=owner_b.public_id)
    with pytest.raises(Http404) as missing:
        get_for_actor_or_404(VendorMember, key_a, public_id=uuid.uuid4())
    assert str(theirs.value) == str(missing.value)
    assert get_for_actor_or_404(VendorMember, StubCredential(None), pk=owner_b.pk) == owner_b


def test_a_credential_is_never_a_member() -> None:
    """Member helpers are for signed-in users: a credential must go through `for_actor`."""
    with pytest.raises(TypeError, match="for_actor"):
        Vendor.objects.for_member(StubCredential(None))  # type: ignore[arg-type]


# --- get_vendor_object_or_404


def test_get_vendor_object_returns_the_members_own_object(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, _ = two_vendors
    translation = owner_a.vendor.translations.get()
    found = get_vendor_object_or_404(
        VendorTranslation, owner_a.user, public_id=translation.public_id
    )
    assert found == translation


def test_another_vendors_object_is_404_like_a_missing_one(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    with pytest.raises(Http404) as theirs:
        get_vendor_object_or_404(VendorMember, owner_a.user, public_id=owner_b.public_id)
    with pytest.raises(Http404) as missing:
        get_vendor_object_or_404(VendorMember, owner_a.user, public_id=uuid.uuid4())
    assert str(theirs.value) == str(missing.value)  # nothing tells the two apart


def test_get_vendor_object_accepts_a_queryset_or_a_manager(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    staff_a = VendorMemberFactory.create(vendor=owner_a.vendor, role=STAFF)
    by_queryset = get_vendor_object_or_404(
        VendorMember.objects.filter(role=STAFF), owner_a.user, pk=staff_a.pk
    )
    assert by_queryset == staff_a
    assert get_vendor_object_or_404(owner_a.vendor.members, owner_a.user, pk=staff_a.pk) == staff_a
    with pytest.raises(Http404):
        get_vendor_object_or_404(owner_b.vendor.members, owner_a.user, pk=owner_b.pk)


def test_malformed_lookup_values_are_404_not_500(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, _ = two_vendors
    with pytest.raises(Http404):
        get_vendor_object_or_404(Vendor, owner_a.user, public_id="not-a-uuid")
    with pytest.raises(Http404):
        get_vendor_object_or_404(Vendor, owner_a.user, pk="not-a-number")


def test_platform_staff_get_404_from_the_member_helper(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, _ = two_vendors
    staff = UserFactory.create(staff=True)
    with pytest.raises(Http404):
        get_vendor_object_or_404(Vendor, staff, pk=owner_a.vendor.pk)


def test_get_for_actor_lets_staff_reach_any_vendor(
    two_vendors: tuple[VendorMember, VendorMember],
) -> None:
    owner_a, owner_b = two_vendors
    staff = UserFactory.create(platform_staff=True)
    assert get_for_actor_or_404(Vendor, staff, pk=owner_b.vendor.pk) == owner_b.vendor
    assert get_for_actor_or_404(Vendor, owner_a.user, pk=owner_a.vendor.pk) == owner_a.vendor
    with pytest.raises(Http404):
        get_for_actor_or_404(Vendor, owner_a.user, pk=owner_b.vendor.pk)


def test_an_unscoped_source_is_refused_loudly() -> None:
    from apps.accounts.models import User

    with pytest.raises(TypeError, match="not vendor-scoped"):
        get_vendor_object_or_404(User, AnonymousUser(), pk=1)


def test_the_translation_factory_rows_are_scoped_through_their_vendor() -> None:
    translation = VendorTranslationFactory.create()
    assert list(VendorTranslation.objects.for_vendor(translation.vendor)) == [translation]
