"""A registry to purge the public page cache without importing the app that owns it (SEO.6).

`apps.vendors` and `apps.events` change what a public page shows (a storefront lists a vendor's
closures and upcoming events) but sit below `apps.seo` in the dependency graph. They call
`purge_public_pages()`; the SEO app registers the listener that drops the cache. Listeners run
after the surrounding transaction commits (a purge inside a rolled-back dry run never fires), and
a failing listener never breaks the service that asked.
"""

import logging
from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.db.models import Model
from django.db.models.signals import post_delete, post_save

logger = logging.getLogger(__name__)

PurgeListener = Callable[[str], None]
_listeners: list[PurgeListener] = []


def register_purge_listener(listener: PurgeListener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def purge_public_pages(reason: str) -> None:
    """Ask every listener to drop the cached public pages, once the transaction has committed."""
    if not _listeners:
        return
    transaction.on_commit(lambda: _notify(reason))


def _notify(reason: str) -> None:
    for listener in list(_listeners):
        try:
            listener(reason)
        except Exception:
            logger.warning("public page purge listener failed (%s)", reason, exc_info=True)


def purge_on_change(model: type[Model], reason: str) -> None:
    """Purge the public pages whenever a row of `model` is saved or deleted (SEO.6 2b).

    For what a visitor sees on a page and that no CMS signal covers: prices, tax rates, vendors
    and media. A shown price must never be stale, so any change drops the whole cache (the hit
    rate is measured on STAGE before anything finer). Connected per model, never without a
    `sender` (see `apps.seo.signals.connect`). `purge_public_pages` waits for the commit.
    """

    def receiver(sender: Any, **kwargs: Any) -> None:
        purge_public_pages(reason)

    label = model._meta.label_lower
    post_save.connect(receiver, sender=model, weak=False, dispatch_uid=f"public-cache.save.{label}")
    post_delete.connect(
        receiver, sender=model, weak=False, dispatch_uid=f"public-cache.delete.{label}"
    )
