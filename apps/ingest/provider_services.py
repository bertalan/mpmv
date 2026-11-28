"""ING.14: a vendor's AI provider choice and its encrypted key.

The key is write-only: it is stored through `EncryptedCharField` (SEC.3), never returned by a
form, never in an audit snapshot or a ChangeSet (they carry `has_key` and `key_version` only),
and never in an error. `key_for(vendor)` is the one read path, for that vendor's own jobs.
"""

import re
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.crypto import DecryptionError
from apps.core.services import run_mutation
from apps.vendors.models import Vendor
from apps.vendors.services import has_vendor_permission

from . import gemini
from .enrichment import ProviderError, TextEnricher
from .gemini import MODEL_NAME, GeminiEnricher
from .models import AIProvider, EnrichmentVersion, VendorAIProvider
from .provider_http import Transport
from .providers import ClaudeEnricher, OpenAIEnricher

MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 300
_KEY_SHAPE = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII, no space

SAVED = "ingest.provider.saved"
KEY_SET = "ingest.provider.key_set"
KEY_ROTATED = "ingest.provider.key_rotated"
KEY_REMOVED = "ingest.provider.key_removed"


def _snapshot(vendor: Vendor, row: VendorAIProvider | None) -> dict[str, Any]:
    return {
        "vendor": str(vendor.public_id),
        "provider": row.provider if row else "",
        "model": row.model if row else "",
        "has_key": bool(row and row.has_key),
        "key_version": row.key_version if row else 0,
    }


def key_for(vendor: Vendor) -> str | None:
    """The vendor's own key, or None. Only this vendor's row is read."""
    row = VendorAIProvider.objects.filter(vendor=vendor).first()
    return row.api_key if row is not None and row.has_key else None


def save_provider(
    vendor: Vendor,
    *,
    provider: str,
    model: str,
    api_key: str = "",
    remove_key: bool = False,
    actor: User,
    ctx: AuditContext,
    passkey_confirmed: bool,
    dry_run: bool = False,
) -> ChangeSet:
    """Set the vendor's provider and model; a non-blank `api_key` sets or rotates the key.

    A blank key keeps the stored one, unless the provider changes (the old key is deleted, never
    sent to the new provider); `remove_key` deletes it. Owners only (`profile`), and only
    with `passkey_confirmed`: the caller has passed the passkey window or a passkey "once" for
    `ai_provider_key` (the service refuses without it). Invalid input raises
    `ValidationError` naming the field, never the key. The audit action tells what
    happened: `saved` (choice only), `key_set`, `key_rotated`, `key_removed`.
    """
    if not passkey_confirmed:
        # The view gates it (CORE.25 `ai_provider_key`); a caller that skips the view says so.
        raise PermissionDenied("Setting the AI provider needs a passkey confirmation.")
    if not has_vendor_permission(actor, vendor, "profile"):
        raise PermissionDenied("Only the vendor owner chooses the AI provider.")
    errors: dict[str, StrOrPromise] = {}
    if provider not in AIProvider.values:
        errors["provider"] = _("Choose one of the listed providers.")
    if not MODEL_NAME.match(model):
        errors["model"] = _("Use the model name as the provider writes it.")
    if api_key and not (
        MIN_KEY_LENGTH <= len(api_key) <= MAX_KEY_LENGTH and _KEY_SHAPE.match(api_key)
    ):
        errors["api_key"] = _("This does not look like an API key.")
    if errors:
        raise ValidationError(errors)
    with transaction.atomic():
        # Lock the vendor first: two first saves cannot both see "no row" (a lock on a missing
        # row would hold nothing), so the unique constraint never turns into a 500.
        Vendor.objects.select_for_update().get(pk=vendor.pk)
        row = VendorAIProvider.objects.select_for_update().filter(vendor=vendor).first()
        before = _snapshot(vendor, row)
        had_key = bool(row and row.has_key)
        changed_provider = row is not None and row.provider != provider
        target = row or VendorAIProvider(vendor=vendor)
        target.provider, target.model = provider, model
        if api_key:
            action = KEY_ROTATED if had_key else KEY_SET
            target.api_key = api_key
            target.key_version += 1
            target.key_set_at = timezone.now()
        elif had_key and (remove_key or changed_provider):
            # A key belongs to the provider that issued it: a new provider never receives it.
            action = KEY_REMOVED
            target.api_key = ""
        else:
            action = SAVED
        after = _snapshot(vendor, target)
        changes = ChangeSet()
        key = str(vendor.public_id)
        if row is None:
            changes.create("ingest.vendoraiprovider", key, after)
        else:
            changes.update("ingest.vendoraiprovider", key, before, after)
        run_mutation(changes, target.save, ctx=ctx, action=action, dry_run=dry_run)
    return changes


_ENRICHERS: dict[str, Any] = {
    AIProvider.CLAUDE: ClaudeEnricher,
    AIProvider.GEMINI: GeminiEnricher,
    AIProvider.OPENAI: OpenAIEnricher,
}


class NoVendorKey(ProviderError):
    """The vendor chose its own provider but has no key: no enrichment, and a notice for it."""

    notice = _(
        "Your own AI provider has no key, so no AI texts were written. "
        "Enter a key on the AI provider page."
    )


def resolve_enricher(vendor: Vendor, *, transport: Transport | None = None) -> TextEnricher:
    """The enricher for one vendor's drafts: its own provider and key; the platform default
    (`settings.INGEST_ENRICHER`, `fake` in tests and local runs) only for a vendor that never
    chose a provider.

    The vendor's choice never reaches another vendor's drafts. A vendor that chose its own
    provider but has no key (never set, removed, or cleared by a provider change) gets
    `NoVendorKey`, never the platform's real provider: its data does not go to another provider
    without its choice (spec 08). A key that cannot be read raises `ProviderError` too. Callers
    treat both as "no enrichment" (the text stays as it is); `NoVendorKey.notice` is for the
    vendor.
    """
    extra: dict[str, Any] = {} if transport is None else {"transport": transport}
    try:  # the field decrypts while the row loads
        row = VendorAIProvider.objects.filter(vendor=vendor).first()
        api_key = row.api_key if row is not None else ""
    except DecryptionError:
        raise ProviderError("the vendor key cannot be read") from None
    if row is None:
        return gemini.get_enricher(**extra)
    if not api_key:
        raise NoVendorKey("the vendor chose its own provider and has no key")
    return _ENRICHERS[row.provider](api_key, row.model, **extra)  # type: ignore[no-any-return]


def usage_count(vendor: Vendor) -> int:
    """How many answers of the vendor's own provider (its own key) were archived for its drafts.

    The platform's provider is not counted: only archives written by the provider the vendor
    chose, and nothing for a vendor with no key of its own.
    """
    row = VendorAIProvider.objects.filter(vendor=vendor).first()
    if row is None or not row.has_key:
        return 0
    return EnrichmentVersion.objects.filter(
        draft__item__vendor=vendor, provider=row.provider
    ).count()
