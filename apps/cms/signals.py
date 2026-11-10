"""Signal adapters (CMS.5): thin, the work is in `services`."""

from functools import cache
from typing import Any

from django.db import models
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Revision

from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog

from . import services
from .models import LegalPage

SYSTEM = AuditContext(actor_label="system:cms", source=AuditLog.Source.SYSTEM)


def record_published_legal_page(
    sender: type[LegalPage], instance: LegalPage, revision: Revision | None = None, **kwargs: Any
) -> None:
    """Wagtail's `page_published`: record the text as a version, in the publisher's name.

    Aliases are skipped: Wagtail republishes them with the source's revision (`alias=True`),
    so they carry the source's text and date, not a translation with its own history (Q-42).
    """
    if kwargs.get("alias") or instance.alias_of_id is not None:
        return
    user = getattr(revision, "user", None)
    ctx = user_context(user, ip=None) if user is not None else SYSTEM
    services.record_legal_version(instance, ctx=ctx)


@cache
def _has_rich_text(model: type[models.Model]) -> bool:
    return any(isinstance(f, RichTextField | StreamField) for f in model._meta.get_fields())


def sanitise_before_save(sender: type[models.Model], instance: models.Model, **kwargs: Any) -> None:
    """`pre_save`: rich text reaches storage only through the sanitiser (CMS.12)."""
    if not kwargs.get("raw") and _has_rich_text(sender):
        services.sanitise_rich_text_fields(instance, only=kwargs.get("update_fields"))
