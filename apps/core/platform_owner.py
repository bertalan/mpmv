"""Bootstrap of the first platform owner (CORE.23, spec 02 Q-34).

The platform owner is the `core.platform_owner` permission, granted directly to a user. Until the
ARC.25 first-start wizard exists, `manage.py bootstrap_platform_owner` is the way to give it to the
first person after a deploy. Idempotent, and audited like every sensitive mutation.
"""

import uuid
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .audit import AuditContext
from .changeset import ChangeSet
from .models import PLATFORM_OWNER_CODENAME, PlatformOperator
from .services import is_platform_owner, run_mutation

ACTION = "core.platform_owner.grant"
USER_MODEL = "accounts.user"


def _snapshot(*, active: bool, owner: bool) -> dict[str, object]:
    return {"is_active": active, "platform_owner": owner}


def grant_platform_owner(
    email: str, *, ctx: AuditContext, create: bool = False, dry_run: bool = False
) -> ChangeSet:
    """Give the platform owner permission to the user with `email`; no-op when already held.

    `create=True` first creates the account, active and with no usable password (the person sets
    one through the password reset). An unknown or inactive user is refused (`ValidationError`).
    """
    user_model = get_user_model()
    email = email.strip().lower()  # the accounts app stores emails trimmed and lower-cased
    validate_email(email)
    user = user_model.objects.filter(email__iexact=email).first()
    changes = ChangeSet()
    new_key = uuid.uuid4()  # a new user's audit key is its public id, never the address
    if user is None:
        if not create:
            raise ValidationError("No user with that email; pass create to make one.")
        changes.create(USER_MODEL, str(new_key), _snapshot(active=True, owner=True))
    else:
        if not user.is_active:
            raise ValidationError("That user is inactive.")
        if is_platform_owner(user):
            return changes
        if apps.get_model("vendors", "VendorMember").objects.filter(user=user).exists():
            raise ValidationError(
                "That user is a vendor member. Platform roles and vendor membership never mix "
                "(spec 02 Roles): the user would be refused by both Manager spaces."
            )
        changes.update(
            USER_MODEL,
            str(user.public_id),
            _snapshot(active=True, owner=False),
            _snapshot(active=True, owner=True),
        )

    def apply() -> None:
        target: Any = user
        if target is None:
            target = user_model(email=email, is_active=True, public_id=new_key)
            target.set_unusable_password()
            target.full_clean(exclude=["password"])
            target.save()
        target.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=PlatformOperator._meta.app_label,
                codename=PLATFORM_OWNER_CODENAME,
            )
        )

    return run_mutation(changes, apply, ctx=ctx, action=ACTION, dry_run=dry_run)
