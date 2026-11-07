from typing import Any

from django.apps import AppConfig
from django.core import checks
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.signals import post_migrate


def _sync_locales(using: str = DEFAULT_DB_ALIAS, **kwargs: Any) -> None:
    from wagtail.models import Locale

    from . import services

    # A partial migrate (an older target) may not have created Wagtail's tables yet.
    if Locale._meta.db_table in connections[using].introspection.table_names():
        services.sync_locales(using=using)


class CmsConfig(AppConfig):
    name = "apps.cms"
    label = "cms"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). Wagtail keeps a revision of every page edit
    # and logs page and snippet actions in its own history (Reports > Site history).
    audited_models = ()
    unaudited_models = {
        "homepage": "Wagtail page: editor content with revisions and Wagtail's action log",
        "standardpage": "Wagtail page: editor content with revisions and Wagtail's action log",
        "navigationmenu": "Wagtail snippet: menu content, logged in Wagtail's action log",
        # Installed only for wagtail-localize's editor (I18N.3); its menu is hidden.
        "wagtaildocs.document": "Wagtail document: editor upload, logged in Wagtail's action log",
        # django-taggit (a Wagtail dependency) registers its tags in the staff admin.
        "taggit.tag": "free-text content tags, no security or money impact",
        "taggit.taggeditem": "a tag's assignment to an object (inline of the tag admin)",
    }

    def ready(self) -> None:
        from .checks import check_base_url

        checks.register(check_base_url, deploy=True)
        # After every migrate (and every test database flush): one Locale per configured
        # language, so adding a language to settings.LANGUAGES needs no migration (spec 09).
        post_migrate.connect(_sync_locales, sender=self, dispatch_uid="cms.sync_locales")
