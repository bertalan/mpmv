"""What drops the anonymous page cache (SEO.6, docs/dev/page-cache.md).

One global generation, not per-URL purging: a page's HTML can depend on its parents (menus,
breadcrumbs, translations), on the site's settings and on the theme, and a wrong guess is a stale
page. Wagtail's own signals cover publish, unpublish, move and delete; a save of a live page
outside the editor, a site, a locale and every site or generic setting (theme, events, the SEO
panel) are caught by the model signals.
"""

from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from wagtail.contrib.settings.models import BaseGenericSetting, BaseSiteSetting
from wagtail.models import (
    CollectionViewRestriction,
    Locale,
    Page,
    PageViewRestriction,
    Site,
)
from wagtail.signals import page_published, page_unpublished, post_page_move

from . import page_cache

_UID = "seo-page-cache-drop"


def drop_cache(**_kwargs: Any) -> None:
    """After the change is committed, never before: a page rendered between the signal and the
    commit would still read the old rows and be stored under the new generation."""
    transaction.on_commit(page_cache.invalidate, robust=True)


def drop_for_content_model(instance: Any = None, **_kwargs: Any) -> None:
    """Model signals reach every model: look only at the ones a public page renders from."""
    if isinstance(instance, Page):
        if instance.live:  # a draft is not served to the public
            drop_cache()
    elif isinstance(
        instance,
        Site
        | Locale
        | BaseSiteSetting
        | BaseGenericSetting
        | PageViewRestriction
        | CollectionViewRestriction,
    ):
        drop_cache()


def connect() -> None:
    for signal in (page_published, page_unpublished, post_page_move):
        signal.connect(drop_cache, dispatch_uid=f"{_UID}.{signal.__class__.__name__}.{id(signal)}")
    post_save.connect(drop_for_content_model, dispatch_uid=f"{_UID}.save")
    post_delete.connect(drop_for_content_model, dispatch_uid=f"{_UID}.delete")
