"""On-demand activation of migrated customer accounts (CORE.19, D-16 amended, L-11).

A migrated account has no usable password (BO-11). It gets one informative service email with
no link and no promotional content (`send_activation_notice`). The activation link exists only
after its holder asks for it on the site, by typing the address of the account
(`request_activation_link`). The answer to that request never depends on the address: a known
account, an unknown address, a staff account and a rate-limited account all get the same page.

The link is a CORE.15 email-bound action link: it signs nobody in, works once, expires after
`ACTION_LINK_MAX_AGE` and is refused once the password is set (its version is a keyed digest
of the account's password hash). The holder chooses the password on the page it opens
(`complete_activation`); the account then signs in normally and MFA applies as for any
account (CORE.4).
"""

import datetime as dt
import hashlib
import logging
from typing import Any

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.urls import reverse

from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.services import consume_rate_limit, enqueue_email

from .models import User, normalize_email_address
from .services import (
    USER_MODEL,
    ActionLinkCheck,
    ActionLinkSpent,
    ActionPurpose,
    _password_stamp,
    complete_password_reset,
    register_action_purpose,
    send_action_link_email,
    spend_email_bound_link,
)

PURPOSE = "accounts.activate"
NOTICE_TEMPLATE = "accounts.activation_notice"
LINK_TEMPLATE = "accounts.activation_link"
NOTICE_SENT = "accounts.activation.notice_sent"
REQUEST_LIMITED = "accounts.activation.request_limited"
ACTIVATED = "accounts.activation.completed"
ACCOUNT_RATE_WINDOW = dt.timedelta(hours=1)
_log = logging.getLogger("mpmv.accounts.activation")


class ActivationLinkInvalid(Exception):
    """The link is not usable: expired, used, replaced, or the account no longer waits."""


def awaiting_activation(user: User | None) -> bool:
    """A migrated or imported account with no usable password. Staff accounts are never
    activated by email: their passwords are set by the audited staff flows."""
    return (
        user is not None
        and user.is_active
        and not user.has_usable_password()
        and not user.is_staff
        and not user.is_superuser
    )


def _user_of(object_key: str) -> User | None:
    try:
        return User.objects.filter(public_id=object_key).first()
    except (ValueError, TypeError):  # not a UUID
        return None


def _version_of(object_key: str) -> str | None:
    user = _user_of(object_key)
    return _password_stamp(user) if user is not None and awaiting_activation(user) else None


def _email_of(object_key: str) -> str | None:
    user = _user_of(object_key)
    return user.email if user is not None and awaiting_activation(user) else None


def activation_path(object_key: str) -> str:
    return reverse("activation:set_password", args=[object_key])


def register_activation_purpose() -> None:
    register_action_purpose(
        ActionPurpose(
            name=PURPOSE,
            version_of=_version_of,
            url_of=activation_path,
            email_of=_email_of,
        )
    )


def send_activation_notice(user: User, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """The one informative email to a migrated account: no link, no promotion.

    At most one per account, ever (idempotency key); an account that already has a password,
    or a staff account, gets none.
    """
    if not awaiting_activation(user):
        return ChangeSet()
    key = str(user.public_id)
    changes = enqueue_email(
        recipient=user.email,
        template_key=NOTICE_TEMPLATE,
        language=user.preferred_language,
        context={},
        idempotency_key=f"{NOTICE_TEMPLATE}:{key}",
        dry_run=dry_run,
    )
    if not dry_run and not changes.is_empty:
        record(ctx, NOTICE_SENT, USER_MODEL, key, None, None)
    return changes


def _account_limit_key(address: str) -> str:
    return "activation:account:" + hashlib.sha256(address.encode()).hexdigest()[:32]


def request_activation_link(email: str, *, ctx: AuditContext) -> bool:
    """The holder typed `email` on the "Activate your account" page.

    Returns whether a link was queued; callers must NOT show that: the page is the same
    either way. At most `ACTIVATION_LINKS_PER_HOUR` links per address and hour (counted for
    unknown addresses too, so the limit tells nothing); a limited request of a known account
    is audited.
    """
    address = normalize_email_address(email)
    wait = consume_rate_limit(
        _account_limit_key(address),
        limit=settings.ACTIVATION_LINKS_PER_HOUR,
        window=ACCOUNT_RATE_WINDOW,
    )
    user = User.objects.filter(email=address).first()
    if user is None or not awaiting_activation(user):
        _log.info("activation requested for an address with nothing to activate")
        return False
    if wait:
        record(ctx, REQUEST_LIMITED, USER_MODEL, str(user.public_id), None, None)
        return False
    changes = send_action_link_email(
        purpose=PURPOSE,
        object_key=str(user.public_id),
        version=_password_stamp(user),
        template_key=LINK_TEMPLATE,
        context={},
        language=user.preferred_language,
        ctx=ctx,
    )
    return not changes.is_empty


def complete_activation(link: ActionLinkCheck, *, password: str, ctx: AuditContext) -> User:
    """The holder opened the emailed link and chose a password.

    One transaction: the link is spent, the password is validated and set (and audited by
    `complete_password_reset`). A refused password (`ValidationError`) leaves the link usable.
    Raises `ActivationLinkInvalid`.
    """
    claims = link.claims
    if link.refusal is not None or claims is None:
        raise ActivationLinkInvalid(link.refusal or "link not opened")
    with transaction.atomic():
        user = _user_of(claims.object_key)
        if user is None:
            raise ActivationLinkInvalid("no such account")
        locked = User.objects.select_for_update().get(pk=user.pk)
        if not awaiting_activation(locked) or _password_stamp(locked) != claims.version:
            raise ActivationLinkInvalid("nothing to activate")
        validate_password(password, locked)
        try:
            spend_email_bound_link(link, locked, ctx=ctx)
        except ActionLinkSpent as exc:
            raise ActivationLinkInvalid(str(exc)) from exc
        complete_password_reset(locked, password, ctx=ctx)
        snapshot: dict[str, Any] = {"has_usable_password": True}
        record(ctx, ACTIVATED, USER_MODEL, str(locked.public_id), None, snapshot)
    return locked
