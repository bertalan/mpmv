"""ORD.32: a staff draft from a past order, with a one-time payment link (owner 2026-11-18).

Support or the platform owner copies a past order into a draft for the same customer (the
order's account, or the guest's email) at today's prices, availability and shipping, may remove
lines or lower or raise quantities up to the stock available, and sends a one-time link to that
customer. Prices are never set here: a draft line has no price, and the customer's cart takes
today's catalog price when the link is opened. The customer reviews the cart, reads the
pre-contractual information and pays through the normal checkout (ORD.3, ORD.4, PAY.1); only
then is an order placed. A draft reserves no stock and writes no ledger line. Staff never place
or pay for the customer and never sign in as them (ORD.22: no impersonation).

- The link goes only to the source order's account email, or to the guest's email for a guest
  order; `send_link` takes no address. At most `engine.DRAFT_MAX_SENDS_PER_DAY` sends per draft
  and business day. Each send makes a new token and so revokes the older link.
- The token is random, carries no personal data and is stored only as its SHA-256 digest, with
  the draft version, the expiry (`PlatformOperator.staff_draft_link_days`, default 7) and the
  send times. A change of the draft bumps the version and clears the digest.
- Opening is a POST (a GET or a mail scanner's prefetch changes nothing) and spends the link:
  the draft becomes `opened`. An account draft opens only for that account, signed in; a guest
  draft opens in the visitor's guest cart, or for a signed-in account with the guest's email.
- Every step is audited with the staff role and the mandatory note; a refused opening of a known
  draft is audited too, without the token.
"""

import datetime as dt
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.translation import gettext

from apps.accounts.models import User
from apps.cart import services as cart_services
from apps.cart.models import Cart
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.models import DEFAULT_STAFF_DRAFT_LINK_DAYS
from apps.core.services import (
    PLATFORM_OWNER_PERMISSION,
    NoPlatformOperator,
    OverlappingPlatformOperators,
    audit_changes,
    current_operator,
    enqueue_email,
)

from . import engine, reorder
from .amendment_engine import PLATFORM_OWNER, SUPPORT
from .models import (
    Order,
    OrderLine,
    OrderState,
    PrivacyStatus,
    StaffDraft,
    StaffDraftLine,
    StaffDraftState,
)
from .tz import business_tz

SUPPORT_PERMISSION = "manager.platform_support"

ACTION_CREATED = "orders.staff_draft.created"
ACTION_CHANGED = "orders.staff_draft.changed"
ACTION_SENT = "orders.staff_draft.sent"
ACTION_OPENED = "orders.staff_draft.opened"
ACTION_REFUSED = "orders.staff_draft.link_refused"
LINK_EMAIL = "orders.staff_draft_link"
DRAFT_MODEL = "orders.staffdraft"
LINE_MODEL = "orders.staffdraftline"
NOTE_MAX = 500

# Why the person holding a valid link may not open it here.
SIGN_IN = "sign_in"  # an account draft and nobody signed in: sign in, the link stays valid
OTHER_ACCOUNT = "other_account"  # signed in as someone else: never switched

# Orders that cannot be copied: a checkout not finished, a quote not yet priced.
_NOT_COPYABLE = frozenset({OrderState.CHECKOUT, OrderState.QUOTE_FLOW})


class DraftRefused(Exception):
    """A draft step that cannot be done; the message is for the staff member."""


@dataclass
class DraftResult:
    draft: StaffDraft | None
    skipped: list[reorder.SkippedItem] = field(default_factory=list)
    changeset: ChangeSet = field(default_factory=ChangeSet)
    existing: bool = False  # an open draft of the order already existed: it is returned


@dataclass
class SendResult:
    changeset: ChangeSet
    sends_left: int
    expires_at: dt.datetime | None


@dataclass
class OpenResult:
    refusal: str | None  # engine.DRAFT_LINK_*, SIGN_IN or OTHER_ACCOUNT
    draft: StaffDraft | None = None
    filled: reorder.ReorderResult | None = None


# --- who and why --------------------------------------------------------------------------------


def staff_role(actor: User) -> str:
    """The role a staff member drafts with: the platform owner or Support (spec 07 roles,
    ORD.22). The Catalog, Finance and Configuration roles are refused, as anyone else."""
    if actor.is_active and actor.has_perm(PLATFORM_OWNER_PERMISSION):
        return PLATFORM_OWNER
    if actor.is_active and actor.has_perm(SUPPORT_PERMISSION):
        return SUPPORT
    raise PermissionDenied(gettext("Only Support or the platform owner may prepare a cart."))


def _note(note: str) -> str:
    cleaned = " ".join(str(note or "").split())[:NOTE_MAX]
    if not cleaned:
        raise DraftRefused(gettext("Write a note: why you prepare this cart, at whose request."))
    return cleaned


def _link_days() -> int:
    try:
        return int(current_operator().staff_draft_link_days)
    except (NoPlatformOperator, OverlappingPlatformOperators):
        return DEFAULT_STAFF_DRAFT_LINK_DAYS


def _customer_kind(draft: StaffDraft) -> str:
    return "account" if draft.customer_id else "guest"


def recipient_of(draft: StaffDraft) -> str:
    """The only address a draft's link may go to: the source order's account email, or the
    guest's email of a guest order. Never an address a caller passes."""
    if draft.customer_id is not None:
        return str(draft.customer.email or "")  # type: ignore[union-attr]
    return str(draft.source_order.email or "")


def _snapshot(draft: StaffDraft, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """What the audit shows of a draft: no email, no address, no token."""
    return {
        "source_order": draft.source_order.number,
        "customer": _customer_kind(draft),
        "state": draft.state,
        "version": draft.version,
        "link": "active" if link_active(draft, now=now) else "none",
    }


def link_active(draft: StaffDraft, *, now: dt.datetime | None = None) -> bool:
    """Whether the last link sent still opens the draft (staff see it on the draft page)."""
    return (
        bool(draft.link_digest)
        and engine.draft_link_refusal(
            is_open=draft.state == StaffDraftState.OPEN,
            issued_version=draft.link_version,
            version=draft.version,
            expires_at=draft.link_expires_at,
            now=now or timezone.now(),
        )
        is None
    )


def _line_snapshot(line: StaffDraftLine) -> dict[str, Any]:
    return {
        "draft": str(line.draft.public_id),
        "product": str(line.product.public_id),
        "variant": str(line.variant.public_id) if line.variant_id else "",  # type: ignore[union-attr]
        "quantity": line.quantity,
    }


def _locked(draft: StaffDraft) -> StaffDraft:
    found = (
        StaffDraft.objects.select_for_update(of=("self",))
        .select_related("source_order", "customer")
        .get(pk=draft.pk)
    )
    if found.state != StaffDraftState.OPEN:
        raise DraftRefused(gettext("The customer has already opened this cart: it cannot change."))
    return found


# --- staff: draft, change, send ------------------------------------------------------------------


def copyable(order: Order) -> bool:
    return order.privacy_status == PrivacyStatus.ACTIVE and order.state not in _NOT_COPYABLE


def _account_closing(customer_id: int | None) -> bool:
    """The account was erased, has an erasure pending (CMP.4) or is inactive: no draft is made
    or sent for it. The erasure request is reached through the user, without importing it."""
    if customer_id is None:
        return False
    return (
        User.objects.filter(pk=customer_id)
        .filter(models.Q(is_active=False) | models.Q(erasure_request__isnull=False))
        .exists()
    )


def _refuse_closing_account(customer_id: int | None) -> None:
    if _account_closing(customer_id):
        raise DraftRefused(
            gettext(
                "The customer's account is closed or its erasure is in progress: no cart may "
                "be prepared or sent for it."
            )
        )


def open_draft_of(order: Order) -> StaffDraft | None:
    return StaffDraft.objects.filter(source_order=order, state=StaffDraftState.OPEN).first()


def draft_from_order(
    order: Order, *, actor: User, note: str, ctx: AuditContext, dry_run: bool = False
) -> DraftResult:
    """Copy `order` into a draft for the same customer, at today's prices and stock and with
    shipping to the order's country. Items that cannot be bought or shipped today are left out
    with the reason; quantities above today's stock are capped. An open draft of the order is
    returned as it is (one per order)."""
    role = staff_role(actor)
    note = _note(note)
    if not copyable(order):
        raise DraftRefused(gettext("This order cannot be copied into a cart."))
    _refuse_closing_account(order.customer_id)
    with transaction.atomic():
        Order.objects.select_for_update().filter(pk=order.pk).first()
        existing = open_draft_of(order)
        if existing is not None:
            return DraftResult(existing, existing=True)
        lines = list(
            OrderLine.objects.filter(vendor_order__order=order).order_by("vendor_order_id", "id")
        )
        items = reorder.order_items(lines)
        outcomes = reorder.plan_items(
            items, in_cart={}, country=order.shipping_country, language=order.language
        )
        result = DraftResult(None)
        draft = StaffDraft(source_order=order, customer=order.customer, created_by=actor)
        kept: list[StaffDraftLine] = []
        for item, outcome in zip(items, outcomes, strict=True):
            if outcome.skipped or item.product is None:
                result.skipped.append(
                    reorder.SkippedItem(item.title, item.variant_label, outcome.skipped)
                )
                continue
            kept.append(
                StaffDraftLine(
                    draft=draft,
                    product=item.product,
                    variant=item.variant,
                    title=item.title,
                    variant_label=item.variant_label,
                    quantity=outcome.quantity,
                )
            )
        draft.skipped = [
            {"title": s.title, "variant": s.variant_label, "reason": s.reason}
            for s in result.skipped
        ]
        result.draft = draft
        result.changeset.create(
            DRAFT_MODEL,
            str(draft.public_id),
            {**_snapshot(draft), "role": role, "note": note, "skipped": draft.skipped},
        )
        for line in kept:
            result.changeset.create(LINE_MODEL, str(line.public_id), _line_snapshot(line))
        if dry_run:
            return result
        draft.save()
        for line in kept:
            line.draft = draft
        StaffDraftLine.objects.bulk_create(kept)
        audit_changes(result.changeset, ctx=ctx, action=ACTION_CREATED)
    return result


def draft_lines(draft: StaffDraft) -> list[StaffDraftLine]:
    return list(
        StaffDraftLine.objects.filter(draft=draft)
        .select_related("product__vendor__connected_account", "variant")
        .order_by("id")
    )


def line_limit(line: StaffDraftLine) -> int | None:
    """The most units of the line's item a cart may hold today; None when it is not for sale."""
    try:
        return cart_services.item_limit(line.product, line.variant)
    except ValidationError:
        return None


def set_quantities(
    draft: StaffDraft,
    quantities: Mapping[uuid.UUID, int],
    *,
    actor: User,
    note: str,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Change the quantities of the draft's lines (0 removes a line), each up to the stock
    available today. Only quantities: a draft has no price to change. Any change revokes the
    link already sent (the version goes up)."""
    role = staff_role(actor)
    note = _note(note)
    changeset = ChangeSet()
    with transaction.atomic():
        locked = _locked(draft)
        lines = {line.public_id: line for line in draft_lines(locked)}
        unknown = set(quantities) - set(lines)
        if unknown:
            raise DraftRefused(gettext("A line of this cart no longer exists. Reload the page."))
        removed: list[StaffDraftLine] = []
        changed: list[StaffDraftLine] = []
        for key, quantity in quantities.items():
            line = lines[key]
            if quantity < 0:
                raise DraftRefused(gettext("A quantity cannot be negative."))
            if quantity == line.quantity:
                continue
            if quantity == 0:
                changeset.delete(LINE_MODEL, str(line.public_id), _line_snapshot(line))
                removed.append(line)
                continue
            limit = line_limit(line)
            if limit is None:
                raise DraftRefused(
                    gettext("%(item)s is not for sale any more: remove it.") % {"item": line.title}
                )
            if quantity > limit:
                raise DraftRefused(
                    gettext("%(item)s: only %(limit)d available.")
                    % {"item": line.title, "limit": limit}
                )
            before = _line_snapshot(line)
            line.quantity = quantity
            changeset.update(LINE_MODEL, str(line.public_id), before, _line_snapshot(line))
            changed.append(line)
        if changeset.is_empty:
            return changeset
        before = _snapshot(locked)
        locked.version += 1  # the link sent for the old version no longer opens
        changeset.update(
            DRAFT_MODEL,
            str(locked.public_id),
            before,
            {**_snapshot(locked), "role": role, "note": note},
        )
        if dry_run:
            return changeset
        for line in removed:
            line.delete()
        for line in changed:
            line.save(update_fields=["quantity", "updated_at"])
        locked.save(update_fields=["version", "updated_at"])
        audit_changes(changeset, ctx=ctx, action=ACTION_CHANGED)
    draft.refresh_from_db()
    return changeset


def link_url(token: str) -> str:
    return str(settings.SITE_BASE_URL).rstrip("/") + reverse("staff_draft_link", args=[token])


def sends_left(draft: StaffDraft, *, now: dt.datetime | None = None) -> int:
    return engine.draft_sends_left(draft.send_times, now=now or timezone.now(), tz=business_tz())


def send_link(
    draft: StaffDraft, *, actor: User, note: str, ctx: AuditContext, dry_run: bool = False
) -> SendResult:
    """Email a new one-time link to the draft's customer: the source order's account email, or
    the guest's email. It revokes the link sent before. At most 3 sends per draft and day."""
    role = staff_role(actor)
    note = _note(note)
    now = timezone.now()
    with transaction.atomic():
        locked = _locked(draft)
        order = locked.source_order
        if order.privacy_status != PrivacyStatus.ACTIVE:
            raise DraftRefused(gettext("This order's customer data is no longer kept."))
        _refuse_closing_account(locked.customer_id)
        lines = draft_lines(locked)
        if not lines:
            raise DraftRefused(gettext("The cart is empty: there is nothing to send."))
        recipient = recipient_of(locked)
        if not recipient:
            raise DraftRefused(gettext("The customer has no email address to send the link to."))
        left = sends_left(locked, now=now)
        if left <= 0:
            raise DraftRefused(
                gettext("The link was already sent %(count)d times today. Try again tomorrow.")
                % {"count": engine.DRAFT_MAX_SENDS_PER_DAY}
            )
        token = secrets.token_urlsafe(32)
        expires_at = now + dt.timedelta(days=_link_days())
        before = {**_snapshot(locked), "sends_today": engine.DRAFT_MAX_SENDS_PER_DAY - left}
        locked.link_digest = engine.draft_token_digest(token)
        locked.link_version = locked.version
        locked.link_expires_at = expires_at
        locked.send_times = [*locked.send_times, now.isoformat()]
        changeset = ChangeSet()
        changeset.update(
            DRAFT_MODEL,
            str(locked.public_id),
            before,
            {
                **_snapshot(locked),
                "sends_today": engine.DRAFT_MAX_SENDS_PER_DAY - left + 1,
                "recipient": _customer_kind(locked),
                "link_expires_at": expires_at,
                "role": role,
                "note": note,
            },
        )
        if dry_run:
            return SendResult(changeset, left, None)
        locked.save(
            update_fields=[
                "link_digest",
                "link_version",
                "link_expires_at",
                "send_times",
                "updated_at",
            ]
        )
        try:
            enqueue_email(
                recipient=recipient,
                template_key=LINK_EMAIL,
                language=order.language,
                idempotency_key=f"staff-draft:{locked.public_id}:{locked.link_digest[:24]}",
                context=_email_context(order, lines, token, expires_at),
            )
        except ValueError as exc:
            raise DraftRefused(gettext("The link could not be emailed to the customer.")) from exc
        audit_changes(changeset, ctx=ctx, action=ACTION_SENT)
    draft.refresh_from_db()
    return SendResult(changeset, left - 1, expires_at)


def expire_links_of_customer(
    user: User, *, now: dt.datetime | None = None, dry_run: bool = False
) -> ChangeSet:
    """Account erasure (CMP.4, #757): every link still open of the drafts made for `user` now
    answers as an expired link, so a one-time link sent before the erasure no longer opens. The
    draft stays open and keeps its digest. The caller (the erasure) owns the transaction and the
    audit; the snapshots hold no email, name or token."""
    stamp = now or timezone.now()
    changeset = ChangeSet()
    rows = (
        StaffDraft.objects.select_related("source_order")
        .select_for_update(of=("self",))
        .filter(customer=user, state=StaffDraftState.OPEN, link_expires_at__gt=stamp)
        .exclude(link_digest="")
        .order_by("id")
    )
    for draft in rows:
        before = _snapshot(draft, now=stamp)
        draft.link_expires_at = stamp
        changeset.update(DRAFT_MODEL, str(draft.public_id), before, _snapshot(draft, now=stamp))
        if not dry_run:
            draft.save(update_fields=["link_expires_at", "updated_at"])
    return changeset


def _email_context(
    order: Order, lines: list[StaffDraftLine], token: str, expires_at: dt.datetime
) -> dict[str, Any]:
    with translation.override(order.language):
        expires = formats.date_format(timezone.localtime(expires_at, business_tz()), "DATE_FORMAT")
    return {
        "order_number": order.number,
        "customer_name": order.customer_name,
        "link": link_url(token),
        "expires": expires,
        "items": [
            {"title": line.title, "variant": line.variant_label, "quantity": line.quantity}
            for line in lines
        ],
    }


# --- the customer: the one-time link ---------------------------------------------------------


def _find(token: str, *, lock: bool = False) -> StaffDraft | None:
    if not token or len(token) > 200:
        return None
    rows = StaffDraft.objects.select_related("source_order", "customer")
    if lock:
        rows = rows.select_for_update(of=("self",))
    digest = engine.draft_token_digest(token)
    return rows.filter(link_digest=digest).first()


def check_link(token: str, *, now: dt.datetime | None = None) -> OpenResult:
    """Read only: whether `token` opens a draft now (for the GET page)."""
    draft = _find(token)
    if draft is None:
        return OpenResult(engine.DRAFT_LINK_UNKNOWN)
    refusal = engine.draft_link_refusal(
        is_open=draft.state == StaffDraftState.OPEN,
        issued_version=draft.link_version,
        version=draft.version,
        expires_at=draft.link_expires_at,
        now=now or timezone.now(),
    )
    return OpenResult(refusal, draft)


def _who_refusal(draft: StaffDraft, user: User | None) -> str | None:
    if draft.customer_id is not None:
        if user is None:
            return SIGN_IN
        return None if user.pk == draft.customer_id else OTHER_ACCOUNT
    if user is None:
        return None
    guest = str(draft.source_order.email or "").strip().casefold()
    return None if guest and str(user.email or "").strip().casefold() == guest else OTHER_ACCOUNT


def _items(draft: StaffDraft) -> list[Any]:
    lines = draft_lines(draft)
    return [
        reorder.item_for(
            (line.product_id, line.variant_id),
            line.title,
            line.variant_label,
            line.quantity,
            line.product,
            line.variant,
        )
        for line in lines
    ]


def open_link(
    token: str,
    *,
    user: User | None,
    cart_for: Callable[[], Cart],
    ctx: AuditContext,
) -> OpenResult:
    """Spend the link: put the draft's items in the customer's cart (`cart_for()`) at today's
    prices and stock, and close the draft. Refused, with nothing changed, when the link is
    unknown, used, expired or older than the draft, or when the person is not the customer
    (an account draft needs that account signed in). The refusal of a known draft is audited."""
    with transaction.atomic():
        draft = _find(token, lock=True)
        if draft is None:
            return OpenResult(engine.DRAFT_LINK_UNKNOWN)
        refusal = engine.draft_link_refusal(
            is_open=draft.state == StaffDraftState.OPEN,
            issued_version=draft.link_version,
            version=draft.version,
            expires_at=draft.link_expires_at,
            now=timezone.now(),
        ) or _who_refusal(draft, user)
        if refusal is not None:
            if refusal != SIGN_IN:
                record(ctx, ACTION_REFUSED, DRAFT_MODEL, str(draft.public_id), None, {
                    "reason": refusal
                })  # fmt: skip
            return OpenResult(refusal, draft)
        cart = cart_for()
        filled = reorder.fill_cart(
            cart,
            _items(draft),
            country=cart.country_hint or draft.source_order.shipping_country,
            ctx=ctx,
        )
        before = _snapshot(draft)
        draft.state = StaffDraftState.OPENED  # single use: the same link is refused from now on
        draft.opened_at = timezone.now()
        draft.save(update_fields=["state", "opened_at", "updated_at"])
        record(
            ctx,
            ACTION_OPENED,
            DRAFT_MODEL,
            str(draft.public_id),
            before,
            {**_snapshot(draft), "added": len(filled.added), "skipped": len(filled.skipped)},
        )
    return OpenResult(None, draft, filled)
