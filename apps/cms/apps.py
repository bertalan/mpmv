from typing import Any

from django.apps import AppConfig
from django.apps import apps as django_apps
from django.core import checks
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.signals import post_migrate, post_save

from . import roles


def _sync_editor_roles(using: str = DEFAULT_DB_ALIAS, **kwargs: Any) -> None:
    from . import editor_roles

    editor_roles.sync_editor_roles(using=using)


def _grant_new_page(
    sender: Any, instance: Any, created: bool, raw: bool = False, **kw: Any
) -> None:
    if created and not raw:
        from . import editor_roles

        editor_roles.grant_for_new_page(instance, using=kw.get("using") or DEFAULT_DB_ALIAS)


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
        "editorroleseed": "marker row that a role was seeded once; no security data of its own",
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
        from .signals import record_published_legal_page

        checks.register(check_base_url, deploy=True)
        # After every migrate (and every test database flush): one Locale per configured
        # language, so adding a language to settings.LANGUAGES needs no migration (spec 09).
        post_migrate.connect(_sync_locales, sender=self, dispatch_uid="cms.sync_locales")
        # Every published text of a legal page is kept as a version (CMS.5).
        page_published.connect(
            record_published_legal_page, sender=LegalPage, dispatch_uid="cms.legal_versions"
        )
        # Editor roles (CORE.8, Q-58): groups and page grants after every migrate, and a grant
        # for every page created later in a part of the tree a role is bound to.
        post_migrate.connect(_sync_editor_roles, sender=self, dispatch_uid="cms.editor_roles")
        from . import workflow

        workflow.install()  # nobody approves their own submission
        for label in roles.ROLE_PAGE_MODELS:
            post_save.connect(
                _grant_new_page,
                sender=django_apps.get_model(label),
                dispatch_uid=f"cms.role.{label}",
            )
