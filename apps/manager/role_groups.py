"""Role groups in the database (CORE.18): created from `roles.ROLES`, checked for drift.

`sync_role_groups` is idempotent and takes the model classes so the data migration can pass its
historical ones. The system check `manager.E004` (`check_role_group_drift`) fails when a group is
missing or its permissions differ from `roles.py`, a Django admin group edit included.
"""

from typing import Any

from . import roles


def _permission(models: tuple[Any, Any, Any], label: str, *, create: bool) -> Any:
    _, permission_model, content_type_model = models
    app_label, codename = label.split(".")
    if not create:
        return permission_model.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
    model = {"core": "platformoperator", "vendors": "vendor", "manager": "platformaccess"}[
        app_label
    ]
    content_type, _ = content_type_model.objects.get_or_create(app_label=app_label, model=model)
    permission, _ = permission_model.objects.get_or_create(
        content_type=content_type, codename=codename, defaults={"name": codename}
    )
    return permission


def sync_role_groups(group_model: Any, permission_model: Any, content_type_model: Any) -> list[str]:
    """Make every role's group hold exactly the role's permissions; returns the groups changed.

    Creates a missing permission row (a fresh database gets them at post_migrate, later than the
    data migration). Membership is never touched. A second run changes nothing and returns [].
    """
    models = (group_model, permission_model, content_type_model)
    changed = []
    for r in roles.ROLES:
        group, created = group_model.objects.get_or_create(name=r.group)
        want = {_permission(models, label, create=True).pk for label in r.permissions}
        have = set(group.permissions.values_list("pk", flat=True))
        if want != have:
            group.permissions.set(want)
        if created or want != have:
            changed.append(r.group)
    return changed


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


def user_roles(user: Any) -> frozenset[str]:
    """The platform roles `user` holds through group membership (active users only)."""
    if not (getattr(user, "is_authenticated", False) and user.is_active):
        return frozenset()
    names = user.groups.filter(name__in=roles.group_names()).values_list("name", flat=True)
    return frozenset(key for name in names if (key := roles.role_for_group(name)))
