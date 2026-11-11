"""Role groups in the database (CORE.18): created from `roles.ROLES`, checked for drift.

`sync_role_groups` is idempotent and takes the model classes so the data migration can pass its
historical ones. The system check `manager.E004` (`check_role_group_drift`) fails when a group is
missing or its permissions differ from `roles.py`, a Django admin group edit included.
"""

from typing import Any

from . import roles


def _permission(models: tuple[Any, Any, Any], label: str, get_model: Any) -> Any:
    _, permission_model, content_type_model = models
    app_label, codename = label.split(".")
    model = {"core": "platformoperator", "vendors": "vendor", "manager": "platformaccess"}[
        app_label
    ]
    content_type, _ = content_type_model.objects.get_or_create(app_label=app_label, model=model)
    # The name is the model's Meta label, as Django's own post_migrate step would write it.
    names = dict(get_model(app_label, model)._meta.permissions)
    permission, _ = permission_model.objects.get_or_create(
        content_type=content_type,
        codename=codename,
        defaults={"name": names.get(codename, codename)},
    )
    return permission


def sync_role_groups(
    group_model: Any, permission_model: Any, content_type_model: Any, get_model: Any
) -> list[str]:
    """Make every role's group hold exactly the role's permissions; returns the groups changed.

    `get_model(app_label, model)` is `django.apps.apps.get_model`, or the migration's. Creates a
    missing permission row (a fresh database gets them at post_migrate, later than the
    data migration). Membership is never touched. A second run changes nothing and returns [].
    """
    models = (group_model, permission_model, content_type_model)
    changed = []
    for r in roles.ROLES:
        group, created = group_model.objects.get_or_create(name=r.group)
        want = {_permission(models, label, get_model).pk for label in r.permissions}
        have = set(group.permissions.values_list("pk", flat=True))
        if want != have:
            group.permissions.set(want)
        if created or want != have:
            changed.append(r.group)
    return changed


def refuse_adopting_populated_groups(group_model: Any) -> None:
    """The first seeding adopts a same-name group only when it is empty (no silent grant)."""
    for r in roles.ROLES:
        group = group_model.objects.filter(name=r.group).first()
        if group is not None and group.user_set.exists():
            raise RuntimeError(
                f"The group {r.group!r} already exists and has members: refusing to adopt it as a "
                "platform role. Rename it or empty it, then migrate again."
            )


def role_drift() -> list[str]:
    """One message per role whose group is missing or differs from `roles.py`."""
    from django.contrib.auth.models import Group

    problems = []
    for r in roles.ROLES:
        group = Group.objects.filter(name=r.group).first()
        if group is None:
            problems.append(f"{r.group}: group missing")
            continue
        have = {
            f"{app}.{code}"
            for app, code in group.permissions.values_list("content_type__app_label", "codename")
        }
        want = set(r.permissions)
        if have != want:
            problems.append(
                f"{r.group}: missing {sorted(want - have)}, unexpected {sorted(have - want)}"
            )
    return problems


def role_conflicts() -> list[str]:
    """Users in a role group who are vendor members or have no admin access (manager.E005)."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from apps.vendors.models import VendorMember

    members = VendorMember.objects.values("user_id")
    bad = (
        get_user_model()
        .objects.filter(groups__name__in=roles.group_names())
        .filter(Q(pk__in=members) | Q(is_staff=False, is_superuser=False))
        .distinct()
    )
    return [
        f"{user.get_username()} is in a platform role group but is a vendor member or has no "
        "admin access; platform roles never mix with vendor membership"
        for user in bad
    ]


def user_roles(user: Any) -> frozenset[str]:
    """The platform roles `user` holds through group membership.

    Active users with admin access only, and never a vendor member: a customer or a member put
    in a role group gets no role powers (spec 02 Roles, CORE.18).
    """
    from apps.vendors.services import is_vendor_member

    if not (getattr(user, "is_authenticated", False) and user.is_active):
        return frozenset()
    if not (user.is_staff or user.is_superuser) or is_vendor_member(user):
        return frozenset()
    names = user.groups.filter(name__in=roles.group_names()).values_list("name", flat=True)
    return frozenset(key for name in names if (key := roles.role_for_group(name)))


def in_role_group(user: Any) -> bool:
    """Member of a platform role group, whatever else is true of the account."""
    if not (getattr(user, "is_authenticated", False) and user.is_active):
        return False
    return bool(user.groups.filter(name__in=roles.group_names()).exists())


def effective_roles(user: Any) -> frozenset[str]:
    """`user_roles` plus the owner role of an owner who holds the permission directly.

    The first platform owner is created with the permission itself (ARC.25, the bootstrap
    command), not with the group; both are the owner role in the matrix. A vendor member holds
    none (`core.services.holds_permission`).
    """
    from apps.core.services import holds_permission

    from .roles import OWNER, PLATFORM_OWNER_PERMISSION

    held = user_roles(user)
    if OWNER not in held and holds_permission(user, PLATFORM_OWNER_PERMISSION):
        return held | {OWNER}
    return held
