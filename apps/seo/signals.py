"""What drops the anonymous page cache (SEO.6, docs/dev/page-cache.md).

One global generation, not per-URL purging: a page's HTML can depend on its parents (menus,
breadcrumbs, translations), on the site's settings and on the theme, and a wrong guess is a stale
page. Wagtail's own signals cover publish, unpublish, move and delete; a save of a live page
outside the editor, a site, a locale and every site or generic setting (theme, events, the SEO
panel) are caught by the model signals.
"""

from typing import Any

from django.apps import apps as django_apps
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
from wagtail.signals import (
    page_published,
    page_unpublished,
    post_page_move,
    published,
    unpublished,
)

from apps.cms.models import BlogAuthor, ContentSnippet, NavigationMenu
from apps.core import public_cache
from apps.core.models import PlatformOperator

from . import page_cache

_UID = "seo-page-cache-drop"


def drop_cache(**_kwargs: Any) -> None:
    """After the change is committed, never before: a page rendered between the signal and the
    commit would still read the old rows and be stored under the new generation."""
    transaction.on_commit(page_cache.invalidate, robust=True)


CONTENT_MODELS = (
    PlatformOperator,  # the legal and tax text of the footer
    NavigationMenu,  # the header and footer links of every page
    ContentSnippet,  # a text placed in any page body (a draft is skipped, see below)
    BlogAuthor,  # the byline and the author box of a post
    Page,
    Site,
    Locale,
    BaseSiteSetting,
    BaseGenericSetting,
    PageViewRestriction,
    CollectionViewRestriction,
)


# What Wagtail's `save_revision` writes on the live row (an editor's autosave): nothing a visitor
# sees changes until the revision is published, which fires `page_published`.
REVISION_BOOKKEEPING = frozenset(
    {
        "latest_revision",
        "latest_revision_created_at",
        "draft_title",
        "has_unpublished_changes",
        "wagtail_admin_comments",
    }
)


def drop_for_content_model(instance: Any = None, **kwargs: Any) -> None:
    """A change to a model a public page renders from."""
    if isinstance(instance, Page | ContentSnippet):
        if not instance.live:
            return  # a draft is not served to the public
        fields = kwargs.get("update_fields")
        if fields and set(fields) <= REVISION_BOOKKEEPING:
            return  # an autosave: purge on publish only
    drop_cache()


def purge_on_request(reason: str) -> None:
    """The listener `apps.core.public_cache` calls for vendors and events (already on commit)."""
    page_cache.invalidate()


def connect() -> None:
    """Connect per model, never without a `sender`: a `post_delete` receiver on every model makes
    Django load and delete each row by primary key instead of one fast `DELETE ... WHERE`, which
    slows bulk deletes everywhere (and changes the SQL of unrelated code, e.g. the API's refused-key
    clean-up)."""
    public_cache.register_purge_listener(purge_on_request)
    for signal in (page_published, page_unpublished, post_page_move):
        signal.connect(drop_cache, dispatch_uid=f"{_UID}.{signal.__class__.__name__}.{id(signal)}")
    for signal in (published, unpublished):  # a snippet's publish and unpublish
        signal.connect(
            drop_cache, sender=ContentSnippet, dispatch_uid=f"{_UID}.snippet.{id(signal)}"
        )
    for model in django_apps.get_models():
        if issubclass(model, CONTENT_MODELS):
            post_save.connect(drop_for_content_model, sender=model, dispatch_uid=f"{_UID}.save")
            post_delete.connect(drop_for_content_model, sender=model, dispatch_uid=f"{_UID}.delete")
