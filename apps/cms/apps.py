from typing import Any

from django.apps import AppConfig
from django.core import checks
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.signals import post_migrate, pre_save


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
    audited_models = ("legalpageversion", "legalpagereview")
    unaudited_models = {
        "homepage": "Wagtail page: editor content with revisions and Wagtail's action log",
        "legalpage": "Wagtail page: each published text is an audited LegalPageVersion",
        "vendorrecruitmentpage": "Wagtail page: editor content with revisions and action log",
        "standardpage": "Wagtail page: editor content with revisions and Wagtail's action log",
        "blogindexpage": "Wagtail page: editor content with revisions and Wagtail's action log",
        "blogpostpage": "Wagtail page: editor content with revisions and Wagtail's action log",
        "blogauthor": "Wagtail snippet: a byline name, logged in Wagtail's action log",
        "termlandingpage": "Wagtail page: editor content with revisions and action log",
        "vendorlandingpage": "Wagtail page: editor content with revisions and action log",
        "navigationmenu": "Wagtail snippet: menu content, logged in Wagtail's action log",
        # Installed only for wagtail-localize's editor (I18N.3); its menu is hidden.
        "wagtaildocs.document": "Wagtail document: editor upload, logged in Wagtail's action log",
        # django-taggit (a Wagtail dependency) registers its tags in the staff admin.
        "taggit.tag": "free-text content tags, no security or money impact",
        "taggit.taggeditem": "a tag's assignment to an object (inline of the tag admin)",
    }

    def ready(self) -> None:
        from wagtail.signals import page_published

        from .checks import check_base_url
        from .models import LegalPage
        from .signals import record_published_legal_page, sanitise_before_save

        checks.register(check_base_url, deploy=True)
        # After every migrate (and every test database flush): one Locale per configured
        # language, so adding a language to settings.LANGUAGES needs no migration (spec 09).
        post_migrate.connect(_sync_locales, sender=self, dispatch_uid="cms.sync_locales")
        # Every published text of a legal page is kept as a version (CMS.5).
        page_published.connect(
            record_published_legal_page, sender=LegalPage, dispatch_uid="cms.legal_versions"
        )
        # Rich text written outside the editor is stored with the editor's allow-list (CMS.12).
        pre_save.connect(sanitise_before_save, dispatch_uid="cms.sanitise_rich_text")
        _guard_editor()


def _guard_editor() -> None:
    """The editor opens any stored rich text (CMS.12, F2).

    Wagtail's Draftail converter asserts on stored HTML it would not write (an `<img>` in a
    field without the image feature). Text from an import or a seed can hold such an element, so
    the converter reads the sanitised text: the editor shows what a save would store.
    """
    from wagtail.admin.rich_text.converters.contentstate import ContentstateConverter
    from wagtail.rich_text import features as feature_registry

    from . import engine

    if getattr(ContentstateConverter.from_database_format, "_cms12", False):
        return
    original = ContentstateConverter.from_database_format

    def from_database_format(self: Any, html: str) -> Any:
        features = self.features
        if features is None:
            features = feature_registry.get_default_features()
        return original(self, engine.sanitise_rich_text(html, features))

    from_database_format._cms12 = True  # type: ignore[attr-defined]
    ContentstateConverter.from_database_format = from_database_format
