"""CMS services: locales from settings, the editors' MFA policy, seed pages and menus (CMS.1);
legal page versions and the link to the vendor application (CMS.5).

Pages and menus are edited in the Wagtail admin, which keeps their revisions and its own log of
every edit, so they are not audited here: `seed_site` only adds sample content where it is
missing (local development, STAGE and the e2e suite). Legal page versions are the exception:
they are the record of which text applied when, so each one is audited.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, PermissionsMixin
from django.db import DEFAULT_DB_ALIAS, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import slugify
from wagtail.models import Locale, Page, Site

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.core.services import run_mutation

from . import engine
from .models import HomePage, LegalPage, LegalPageVersion, NavigationMenu, StandardPage
from .seed import PageText, seed_text

CMS_ADMIN_PERMISSION = "wagtailadmin.access_admin"


def sync_locales(using: str = DEFAULT_DB_ALIAS) -> list[str]:
    """Create the Locale of every language in settings.LANGUAGES that has none yet.

    Runs after every `migrate` (CmsConfig.ready) on the database that was migrated, so adding a
    language is a settings entry. Locales of languages removed from settings are kept: their
    pages may still exist. Returns the language codes created.
    """
    locales = Locale.objects.using(using)
    existing = set(locales.values_list("language_code", flat=True))
    missing = [code for code in configured_languages() if code not in existing]
    locales.bulk_create([Locale(language_code=code) for code in missing])
    return missing


def can_access_cms_admin(user: AbstractBaseUser | AnonymousUser) -> bool:
    """MFA policy (CORE.4): whoever may open the Wagtail admin must verify a second factor.

    Wagtail grants its admin through a permission, not `is_staff`, so an editor group without
    staff status is covered here (listed in settings.MFA_REQUIRED_POLICIES).
    """
    return isinstance(user, PermissionsMixin) and user.has_perm(CMS_ADMIN_PERMISSION)


# --- Seed content


@dataclass
class SeedReport:
    """What `seed_site` created, one human-readable line per object."""

    created: list[str] = field(default_factory=list)


def _publish[P: Page](page: P) -> P:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _apply_text(page: Page, text: PageText) -> None:
    page.title = page.draft_title = text.title
    # Home pages are siblings under the tree root, where Wagtail gives each translation its
    # own slug (home, home-it...); they never appear in URLs, so the copy's slug is kept.
    if not isinstance(page, HomePage) or not page.slug:
        page.slug = text.slug or slugify(text.title)
    page.body = [("paragraph", text.body)]  # HomePage and StandardPage


def _root() -> Page:
    root = Page.get_first_root_node()
    if root is None:  # an empty database (e.g. flushed by a transactional test)
        root = Page.add_root(instance=Page(title="Root", slug="root"))
    return root


def _primary_home(report: SeedReport) -> HomePage:
    """The default site's home in the primary language, created with the site if missing."""
    primary = Locale.get_default()
    site = Site.objects.filter(is_default_site=True).select_related("root_page").first()
    if site is not None and isinstance(site.root_page.specific, HomePage):
        home: HomePage = site.root_page.specific
        return home
    home = HomePage.objects.filter(locale=primary, depth=2).first()
    if home is None:
        home = HomePage(locale=primary)
        _apply_text(home, seed_text(primary.language_code).home)
        _root().add_child(instance=home)
        home = _publish(home)
        report.created.append(f"{primary.language_code} HomePage {home.url_path}")
    if site is None:
        Site.objects.create(
            hostname="localhost",
            port=80,
            root_page=home,
            is_default_site=True,
            site_name=settings.WAGTAIL_SITE_NAME,
        )
    else:
        site.root_page = home
        site.save()
    return home


def _translated[P: Page](source: P, locale: Locale, text: PageText, report: SeedReport) -> P:
    existing = source.get_translation_or_none(locale)
    if existing is not None:
        page: P = existing.specific
        return page
    page = source.copy_for_translation(locale).specific
    _apply_text(page, text)
    page = _publish(page)
    report.created.append(f"{locale.language_code} {type(page).__name__} {page.url_path}")
    return page


def _ensure_menus(
    locale: Locale,
    home: HomePage,
    about: StandardPage,
    report: SeedReport,
    *,
    sources: dict[str, NavigationMenu] | None = None,
) -> dict[str, NavigationMenu]:
    """The header and footer menus of `locale`, by location, created when missing.

    A new menu with a menu in `sources` for its location becomes its translation (same
    `translation_key`), as an editor's "translate" would make it.
    """
    layouts: dict[str, list[Page]] = {
        NavigationMenu.Location.HEADER: [about],
        NavigationMenu.Location.FOOTER: [home, about],
    }
    menus: dict[str, NavigationMenu] = {}
    for location, pages in layouts.items():
        menu = NavigationMenu.objects.filter(locale=locale, location=location).first()
        if menu is None:
            menu = NavigationMenu(
                locale=locale,
                location=location,
                links=[("page", {"page": page, "label": ""}) for page in pages],
            )
            source = (sources or {}).get(location)
            if source is not None:
                menu.translation_key = source.translation_key
            menu.save()
            report.created.append(f"{locale.language_code} {location} menu")
        menus[location] = menu
    return menus


@transaction.atomic
def seed_site() -> SeedReport:
    """Make sure every configured language has a home, an About page and its two menus.

    Idempotent: only what is missing is created, and existing pages and menus are never
    changed. The primary-language pages and menus are created first; the other languages get
    Wagtail translations of them (same `translation_key`), with the sample texts of
    `apps.cms.seed`.
    """
    report = SeedReport()
    sync_locales()
    primary = Locale.get_default()
    home = _primary_home(report)
    about_text = seed_text(primary.language_code).about
    about = StandardPage.objects.child_of(home).filter(slug=about_text.slug).first()
    if about is None:
        about = StandardPage(locale=primary)
        _apply_text(about, about_text)
        home.add_child(instance=about)
        about = _publish(about)
        report.created.append(f"{primary.language_code} StandardPage {about.url_path}")
    primary_menus = _ensure_menus(primary, home, about, report)

    for code in configured_languages():
        if code == primary.language_code:
            continue
        locale = Locale.objects.get(language_code=code)
        texts = seed_text(code)
        local_home = _translated(home, locale, texts.home, report)
        local_about = _translated(about, locale, texts.about, report)
        _ensure_menus(locale, local_home, local_about, report, sources=primary_menus)
    return report


# --- Legal pages and vendor recruitment (CMS.5)

LEGAL_VERSION_RECORDED = "cms.legal_page_version.recorded"
# The public vendor application form of VEN.22 (owner decision Q-55), not built yet.
APPLICATION_URL_NAME = "vendors:apply"


def legal_version_snapshot(version: LegalPageVersion) -> dict[str, Any]:
    body = json.dumps(version.content()["body"], sort_keys=True, default=str)
    return {
        "page": version.page_id,
        "translation_key": str(version.page.translation_key),
        "language_code": version.language_code,
        "number": version.number,
        "title": version.title,
        "effective_date": version.effective_date.isoformat(),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "published_by": version.published_by,
    }


def record_legal_version(page: LegalPage, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Record the page's published content as its next version, unless it is unchanged.

    Called when a legal page is published. Versions belong to the page, so to one language: a
    translation keeps its own numbering and effective dates (Q-42).
    """
    with transaction.atomic():
        # Lock the page row so two publishes cannot take the same version number.
        LegalPage.objects.select_for_update().filter(pk=page.pk).exists()
        latest = page.versions.order_by("-number").first()
        current = page.content()
        changeset = ChangeSet()
        if not engine.content_changed(latest and latest.content(), current):
            return changeset
        version = LegalPageVersion(
            page=page,
            number=latest.number + 1 if latest else 1,
            language_code=page.locale.language_code,
            title=page.title,
            effective_date=page.effective_date,
            body=page.body.get_prep_value(),
            published_at=timezone.now(),
            published_by=ctx.actor_label,
        )
        changeset.create(
            "cms.legalpageversion",
            f"{page.pk}/{version.number}",
            legal_version_snapshot(version),
        )
        return run_mutation(
            changeset, version.save, ctx=ctx, action=LEGAL_VERSION_RECORDED, dry_run=dry_run
        )


def vendor_application_url(urlconf: str | None = None) -> str | None:
    """The VEN.22 application form's URL, or None while that route does not exist."""
    try:
        return reverse(APPLICATION_URL_NAME, urlconf=urlconf)
    except NoReverseMatch:
        return None
