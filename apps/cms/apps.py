from typing import Any

from django.apps import AppConfig
from django.apps import apps as django_apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save

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
        "helpentry": "Wagtail snippet: help text (ARC.32), logged in Wagtail's action log",
        # Installed only for wagtail-localize's editor (I18N.3); its menu is hidden.
        "wagtaildocs.document": "Wagtail document: editor upload, logged in Wagtail's action log",
        # django-taggit (a Wagtail dependency) registers its tags in the staff admin.
        "taggit.tag": "free-text content tags, no security or money impact",
        "taggit.taggeditem": "a tag's assignment to an object (inline of the tag admin)",
    }

    def ready(self) -> None:
        from wagtail.signals import page_published

        from .checks import check_base_url, check_vendor_guide_url
        from .models import LegalPage
        from .signals import record_published_legal_page, sanitise_before_save

        checks.register(check_base_url, deploy=True)
        checks.register(check_vendor_guide_url)
        _declare_page_restriction_forms()
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
        # Rich text written outside the editor is stored with the editor's allow-list (CMS.12).
        pre_save.connect(sanitise_before_save, dispatch_uid="cms.sanitise_rich_text")
        # The cached help answers (ARC.32) are dropped when an entry changes.
        from . import help as help_lookup
        from .models import HelpEntry

        post_save.connect(
            help_lookup.drop_cache, sender=HelpEntry, dispatch_uid="cms.help_cache_save"
        )
        post_delete.connect(
            help_lookup.drop_cache, sender=HelpEntry, dispatch_uid="cms.help_cache_delete"
        )
        _guard_editor()


def _declare_page_restriction_forms() -> None:
    """SEC.14: Wagtail's page privacy forms are anonymous POSTs we do not own; the sweep (SEC.13)
    reads their protection from these declarations."""
    from apps.core.public_forms import FormSpec, Kind, Protection, declare_public_form

    templates = ("wagtailcore/password_required.html",)
    declare_public_form(
        "wagtailcore_authenticate_with_password",
        FormSpec(
            Kind.PUBLIC,
            "Open a page shared by password",
            "SEC.14",
            Protection.CAPTCHA,
            rate_limit="per IP bucket, and per restriction on wrong passwords only with a higher "
            "threshold (locking a page needs captcha-solved wrong guesses), in "
            "PageRestrictionProtectionMiddleware",
            templates=templates,
        ),
        view="wagtail.views.authenticate_with_password",
    )
    declare_public_form(
        "wagtailcore_login",
        FormSpec(
            Kind.PUBLIC,
            "Sign in to see a page shared with logged-in users",
            "SEC.14",
            Protection.CAPTCHA,
            rate_limit="per IP bucket in PageRestrictionProtectionMiddleware; lockout in "
            "authenticate() (CORE.5)",
            templates=("wagtailcore/login.html",),
        ),
        view="django.contrib.auth.views.LoginView",
    )


def _guard_editor() -> None:
    """The editor opens any stored rich text (CMS.12, F2).

    Wagtail's Draftail converter asserts on stored HTML it would not write (an `<img>` in a
    field without the image feature). Text from an import or a seed can hold such an element, so
    the converter reads the sanitised text: the editor shows what a save would store.

    Written against Wagtail 8.1 (`wagtail>=8.1,<8.2`). It is verified here, at startup, so a
    Wagtail upgrade that renames the method or changes its behaviour fails the deploy, not the
    first editor who opens such a page.
    """
    from wagtail.admin.rich_text.converters.contentstate import ContentstateConverter
    from wagtail.rich_text import features as feature_registry

    from . import engine

    if not hasattr(ContentstateConverter, "from_database_format"):
        raise ImproperlyConfigured("CMS.12: ContentstateConverter.from_database_format is gone")
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
    try:
        ContentstateConverter(["bold"]).from_database_format("<p>a</p><p><img src=x></p><p>b</p>")
    except Exception as error:
        raise ImproperlyConfigured(f"CMS.12: the editor guard does not hold: {error!r}") from error
