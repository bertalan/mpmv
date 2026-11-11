"""The indexing switch of a site (SEO.8 2/2, D-35).

- `site_allows_indexing(request)` is the policy `apps.core.indexing` calls (registered in
  `SeoConfig.ready`): the stored switch of the request's Wagtail Site, off when there is no row,
  and off when the database cannot be read or the Host is not allowed (it only ever adds
  noindex; Django still answers its own 400 for a bad Host).
- A stored "allow" is dormant under the environment floor (D-35): it takes effect the moment
  the floor is lifted, so staff re-check the switch when a site moves from STAGE to LIVE.
- `set_site_indexing(...)` is the only writer of `SeoSettings`: staff only, audited, dry-run,
  and it refuses to allow indexing while the environment floor is on.
"""

import logging
from typing import Any

from django.core.exceptions import DisallowedHost, PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import HttpRequest
from wagtail.models import Site

from apps.core.audit import AuditContext, user_context
from apps.core.changeset import ChangeSet
from apps.core.indexing import is_noindex_forced
from apps.core.services import save_audited
from apps.vendors.scoping import is_platform_staff

from .models import SeoSettings

logger = logging.getLogger(__name__)

SEO_SETTINGS_CONFIGURED = "seo.settings.configured"
FLOOR_MESSAGE = (
    "Indexing cannot be allowed while MPMV_FORCE_NOINDEX or demo mode is on: "
    "the environment makes every page noindex."
)


def site_for(request: HttpRequest | None) -> Site | None:
    if request is not None:
        found: Site | None = Site.find_for_request(request)
        if found is not None:
            return found
    return Site.objects.filter(is_default_site=True).first()


def site_allows_indexing(request: HttpRequest | None) -> bool:
    """The stored switch of the request's site; never creates a row."""
    try:
        if request is not None:
            request.get_host()  # DisallowedHost: Django answers 400; we only add noindex
        site = site_for(request)
        if site is None:
            return False
        return bool(
            SeoSettings.objects.filter(site=site).values_list("allow_indexing", flat=True).first()
        )
    except DisallowedHost:
        logger.warning("SEO indexing policy: request with a disallowed Host, answering noindex")
        return False
    except DatabaseError:
        return False


def may_edit_seo_settings(user: Any) -> bool:
    return bool(is_platform_staff(user))


def seo_snapshot(setting: SeoSettings) -> dict[str, Any]:
    return {"site": setting.site_id, "allow_indexing": setting.allow_indexing}


def set_site_indexing(
    actor: Any,
    site: Site,
    allow: bool,
    *,
    ctx: AuditContext | None,
    dry_run: bool = False,
) -> ChangeSet:
    """Allow or stop search engines indexing `site`, audited.

    Raises PermissionDenied for anyone but platform staff and ValidationError when asked to
    allow indexing under the environment floor (turning it off there is allowed).
    """
    if not may_edit_seo_settings(actor):
        raise PermissionDenied("only platform staff change the SEO settings")
    if allow and is_noindex_forced():
        raise ValidationError(FLOOR_MESSAGE)
    setting = SeoSettings.objects.filter(site=site).first() or SeoSettings(site=site)
    setting.allow_indexing = allow
    return save_audited(
        setting,
        seo_snapshot,
        key=str(site.pk),
        ctx=ctx if ctx is not None else user_context(actor, ip=None),
        created=SEO_SETTINGS_CONFIGURED,
        updated=SEO_SETTINGS_CONFIGURED,
        dry_run=dry_run,
    )


def golive_indexing_messages(languages: Any = None) -> list[str]:
    """A `golive_readiness` line when the LIVE environment has indexing off for the default site.

    Silent under the environment floor (STAGE, demo): indexing is meant to be off there.
    """
    if is_noindex_forced() or site_allows_indexing(None):
        return []
    return [
        "Search engine indexing is still off: search engines are told to skip every page. "
        "Turn it on in Settings > SEO when the platform opens to the public."
    ]
