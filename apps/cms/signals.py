"""Signal adapters (CMS.5): thin, the work is in `services`."""

from typing import Any

from wagtail.models import Revision

from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog

from . import services
from .models import LegalPage

SYSTEM = AuditContext(actor_label="system:cms", source=AuditLog.Source.SYSTEM)


def record_published_legal_page(
    sender: type[LegalPage], instance: LegalPage, revision: Revision | None = None, **kwargs: Any
) -> None:
    """Wagtail's `page_published`: record the text as a version, in the publisher's name."""
    user = getattr(revision, "user", None)
    ctx = user_context(user, ip=None) if user is not None else SYSTEM
    services.record_legal_version(instance, ctx=ctx)
