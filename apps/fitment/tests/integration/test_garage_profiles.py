"""FIT.5 (1/3): account garage profiles: model, constraint, limits, services, merge and retire."""

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.fitment import garage_profiles as profiles
from apps.fitment import services
from apps.fitment.models import FitmentScheme, GarageProfile
from apps.fitment.services import FitmentError
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    GarageProfileFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="user:test", source="system", ip="127.0.0.1")


@pytest.fixture(autouse=True)
def levels() -> None:
    for position, code in enumerate(["make", "model", "variant"], start=1):
        FitmentLevelFactory.create(position=position, code=code)


@pytest.fixture
def model_node():  # type: ignore[no-untyped-def]
    make = FitmentNodeFactory.create(name="Make One")
    return FitmentNodeFactory.create(name="Model A", parent=make)


def test_a_profile_is_created_with_a_default_name_and_is_not_active(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    applied = profiles.create_profile(user, model_node, ctx=CTX)
    profile = applied.obj
    assert profile is not None
    assert profile.name == "Make One Model A"
    assert profile.is_active is False
    assert profile.last_used_at is not None
    assert not applied.changes.is_empty
    assert AuditLog.objects.filter(action=profiles.PROFILE_CREATED).count() == 1


def test_create_can_make_the_new_profile_the_active_one(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    first = profiles.create_profile(user, model_node, activate=True, ctx=CTX).obj
    second = profiles.create_profile(user, model_node, name="Other", activate=True, ctx=CTX).obj
    assert first is not None
    assert second is not None
    first.refresh_from_db()
    assert (first.is_active, second.is_active) == (False, True)


def test_dry_run_returns_the_changeset_and_writes_nothing(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    applied = profiles.create_profile(user, model_node, dry_run=True, ctx=CTX)
    assert applied.obj is None
    assert not applied.changes.is_empty
    assert GarageProfile.objects.count() == 0
    assert AuditLog.objects.filter(action=profiles.PROFILE_CREATED).count() == 0
    profile = GarageProfileFactory.create(user=user, node=model_node, is_active=True)
    other = GarageProfileFactory.create(user=user, node=model_node)
    for change in (
        profiles.rename_profile(profile, "New", dry_run=True, ctx=CTX),
        profiles.activate_profile(other, dry_run=True, ctx=CTX),
        profiles.reset_garage(user, dry_run=True, ctx=CTX),
        profiles.delete_profile(other, confirmed=True, dry_run=True, ctx=CTX),
    ):
        assert not change.is_empty
    profiles_now = {p.pk: (p.name, p.is_active) for p in GarageProfile.objects.all()}
    assert profiles_now[profile.pk] == (profile.name, True)
    assert profiles_now[other.pk][1] is False
    assert AuditLog.objects.filter(action__startswith="fitment.garage_profile").count() == 0


def test_the_database_allows_one_active_profile_per_user(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    GarageProfileFactory.create(user=user, node=model_node, is_active=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        GarageProfileFactory.create(user=user, node=model_node, is_active=True)
    GarageProfileFactory.create(user=user, node=model_node, is_active=False)
    GarageProfileFactory.create(user=UserFactory.create(), node=model_node, is_active=True)


def test_the_limit_comes_from_the_scheme_and_defaults_to_ten(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    assert FitmentScheme.objects.get().max_profiles == 10
    FitmentScheme.objects.update(max_profiles=2)
    profiles.create_profile(user, model_node, name="A", ctx=CTX)
    profiles.create_profile(user, model_node, name="B", ctx=CTX)
    with pytest.raises(FitmentError):
        profiles.create_profile(user, model_node, name="C", ctx=CTX)
    # another account has its own count
    profiles.create_profile(UserFactory.create(), model_node, name="A", ctx=CTX)


def test_duplicate_counts_against_the_limit(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    FitmentScheme.objects.update(max_profiles=1)
    only = profiles.create_profile(user, model_node, name="A", ctx=CTX).obj
    assert only is not None
    with pytest.raises(FitmentError):
        profiles.duplicate_profile(only, ctx=CTX)


def test_a_profile_needs_a_pickable_node_a_valid_name_and_year(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    retired = FitmentNodeFactory.create(name="Old", status="retired")
    proposed = FitmentNodeFactory.create(name="New", status="proposed")
    for bad in (retired, proposed):
        with pytest.raises(FitmentError):
            profiles.create_profile(user, bad, ctx=CTX)
    with pytest.raises(FitmentError):
        profiles.create_profile(user, model_node, name="x" * 41, ctx=CTX)
    with pytest.raises(FitmentError):
        profiles.create_profile(user, model_node, year=2019, ctx=CTX)  # scheme has no year
    FitmentScheme.objects.update(year_enabled=True)
    assert profiles.create_profile(user, model_node, year=2019, ctx=CTX).obj is not None
    with pytest.raises(FitmentError):
        profiles.create_profile(user, model_node, year=1800, ctx=CTX)


def test_rename_cleans_and_audits(model_node) -> None:  # type: ignore[no-untyped-def]
    profile = GarageProfileFactory.create(node=model_node, name="Old")
    profiles.rename_profile(profile, "  New   name ", ctx=CTX)
    profile.refresh_from_db()
    assert profile.name == "New name"
    assert AuditLog.objects.filter(action=profiles.PROFILE_UPDATED).count() == 1
    with pytest.raises(FitmentError):
        profiles.rename_profile(profile, " ", ctx=CTX)


def test_duplicate_copies_the_node_and_year_and_is_not_active(model_node) -> None:  # type: ignore[no-untyped-def]
    original = GarageProfileFactory.create(node=model_node, name="Car", year=2019, is_active=True)
    copy = profiles.duplicate_profile(original, ctx=CTX).obj
    assert copy is not None
    assert (copy.user_id, copy.node_id, copy.year) == (original.user_id, model_node.pk, 2019)
    assert copy.name == "Car (copy)"
    assert copy.is_active is False
    original.refresh_from_db()
    assert original.is_active is True


def test_activate_switches_the_active_profile_and_reset_keeps_every_profile(model_node) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory.create()
    first = GarageProfileFactory.create(user=user, node=model_node, is_active=True)
    second = GarageProfileFactory.create(user=user, node=model_node)
    profiles.activate_profile(second, ctx=CTX)
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.is_active, second.is_active) == (False, True)
    profiles.reset_garage(user, ctx=CTX)
    assert GarageProfile.objects.filter(user=user).count() == 2
    assert not GarageProfile.objects.filter(user=user, is_active=True).exists()
    profiles.activate_profile(second, ctx=CTX)  # choosing it again makes it active again
    second.refresh_from_db()
    assert second.is_active is True
    assert profiles.reset_garage(UserFactory.create(), ctx=CTX).is_empty


def test_delete_asks_for_confirmation(model_node) -> None:  # type: ignore[no-untyped-def]
    profile = GarageProfileFactory.create(node=model_node)
    with pytest.raises(FitmentError):
        profiles.delete_profile(profile, confirmed=False, ctx=CTX)
    assert GarageProfile.objects.filter(pk=profile.pk).exists()
    profiles.delete_profile(profile, confirmed=True, ctx=CTX)
    assert not GarageProfile.objects.filter(pk=profile.pk).exists()
    assert AuditLog.objects.filter(action=profiles.PROFILE_DELETED).count() == 1


def test_the_audit_trail_holds_no_profile_name(model_node) -> None:  # type: ignore[no-untyped-def]
    profile = GarageProfileFactory.create(node=model_node, name="Secret label")
    profiles.rename_profile(profile, "Other secret", ctx=CTX)
    profiles.delete_profile(profile, confirmed=True, ctx=CTX)
    text = " ".join(str(r.before) + str(r.after) for r in AuditLog.objects.all())
    assert "secret" not in text.lower()


def test_a_merged_node_takes_its_profiles_along(model_node) -> None:  # type: ignore[no-untyped-def]
    target = FitmentNodeFactory.create(name="Model B", parent=model_node.get_parent())
    profile = GarageProfileFactory.create(node=model_node, is_active=True)
    services.merge_nodes(model_node, target, ctx=CTX)
    profile.refresh_from_db()
    assert profile.node_id == target.pk
    assert profile.is_active is True


def test_a_retired_node_keeps_its_profiles_marked(model_node) -> None:  # type: ignore[no-untyped-def]
    profile = GarageProfileFactory.create(node=model_node)
    assert profile.node_retired is False
    services.retire_node(model_node.get_parent(), ctx=CTX)  # retires the subtree
    profile = GarageProfile.objects.select_related("node").get(pk=profile.pk)
    assert profile.node_retired is True
    assert list(profiles.retired_profiles(profile.user)) == [profile]
    with pytest.raises(FitmentError):
        services.delete_node(model_node, ctx=CTX)  # still referenced: never deleted under it
