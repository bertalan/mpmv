"""CMS services: locales from settings, the editors' MFA policy, seed pages and menus (CMS.1);
page translation with wagtail-localize (I18N.3); legal page versions and the link to the vendor
application (CMS.5); the page's image credits (CMS.8); the go-live readiness of the customer
legal and checkout pages, their per-language reviews and placeholders (I18N.9); blog articles
created by scripts and migrations (CMS.3); what term and vendor landing pages show (CMS.4).

Pages and menus are edited in the Wagtail admin, which keeps their revisions and its own log of
every edit, so they are not audited here: `seed_site` only adds sample content where it is
missing (local development, STAGE and the e2e suite). Translators work in wagtail-localize's
editor; the translation services below do the same steps for scripts and migrations (MIG.7),
each with a dry run that rolls everything back and reports the pages it would change. Legal
page versions are the exception to auditing: they are the record of which text applied when,
so each one is audited, and so is each review of a language's text (I18N.9).
"""

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, transaction
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.fields import RichTextField, StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Locale, Page, Site
from wagtail_localize.models import (
    OverridableSegment,
    SegmentOverride,
    Translation,
    TranslationSource,
)
from wagtail_localize.operations import TranslationCreator

from apps.catalog import listings, slugs
from apps.catalog import services as catalog
from apps.catalog.models import Category, Term
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages, hreflang_alternates
from apps.core.image_licences import Credit
from apps.core.money import CURRENCY
from apps.core.services import run_mutation
from apps.vendors.models import Vendor

from . import engine
from .blocks import public_cards
from .models import (
    BlogAuthor,
    BlogIndexPage,
    BlogPostPage,
    HomePage,
    LegalPage,
    LegalPageReview,
    LegalPageVersion,
    NavigationMenu,
    StandardPage,
    TermLandingPage,
    VendorLandingPage,
)
from .seed import PageText, legal_placeholder, seed_text

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
def seed_site(*, legal_placeholders: bool = False) -> SeedReport:
    """Make sure every configured language has a home, an About page and its two menus, and
    with `legal_placeholders` the five go-live legal pages (I18N.9, `_seed_legal_placeholders`).

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
    if legal_placeholders:
        _seed_legal_placeholders(home, report)
    return report


# --- Page translation (I18N.3)

PAGE = "cms.page"


def _snapshot(page: Page) -> dict[str, Any]:
    """What a translation changes on a page: texts, the main image, the body.

    wagtail-localize publishes a translation once the transaction commits, so the live state is
    not part of it.
    """
    page = page.specific
    body = getattr(page, "body", None)
    return {
        "title": page.title,
        "slug": page.slug,
        "image": getattr(page, "image_id", None),
        "body": json.dumps(body.raw_data, sort_keys=True, default=str) if body else "",
    }


def _current(page: Page) -> Page:
    """The page as stored: a caller's copy may predate a publish (its `live` flag included)."""
    current: Page = Page.objects.get(pk=page.pk).specific
    return current


def _key(page: Page, locale: Locale) -> str:
    return f"{page.translation_key}:{locale.language_code}"


def _in_savepoint(dry_run: bool, work: Callable[[ChangeSet], None]) -> ChangeSet:
    """Run `work` in a savepoint, rolled back when `dry_run`: the changes are reported only."""
    changes = ChangeSet()
    with transaction.atomic():
        work(changes)
        if dry_run:
            transaction.set_rollback(True)
    return changes


def translate_page(
    page: Page,
    language_codes: Iterable[str],
    *,
    user: AbstractBaseUser | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Create synced wagtail-localize translations of `page` in the languages it lacks.

    Each copy goes under the translation of the page's parent (the language's home page) and
    starts with the source texts and images, published when the source is. Languages that
    already have the page, and the page's own language, are left alone.
    """
    page = _current(page)
    codes = [code for code in dict.fromkeys(language_codes) if code != page.locale.language_code]
    missing = [
        locale
        for locale in Locale.objects.filter(language_code__in=codes)
        if page.get_translation_or_none(locale) is None
    ]

    def work(changes: ChangeSet) -> None:
        if not missing:
            return
        TranslationCreator(user, missing).create_translations(page)
        for locale in missing:
            changes.create(PAGE, _key(page, locale), _snapshot(page.get_translation(locale)))

    return _in_savepoint(dry_run, work)


def sync_page_translations(
    page: Page, *, user: AbstractBaseUser | None = None, dry_run: bool = False
) -> ChangeSet:
    """Bring the source page's current content into its translations ("Sync translated pages").

    Published string translations and image overrides are kept; texts without a translation
    and images without an override follow the source. A translation is published again when
    it is live.
    """
    page = _current(page)

    def work(changes: ChangeSet) -> None:
        source, _created = TranslationSource.update_or_create_from_instance(page)
        translations = source.translations.filter(enabled=True).select_related("target_locale")
        for translation in translations:
            target = translation.get_target_instance()
            before = _snapshot(target)
            translation.save_target(user=user, publish=target.live)
            after = _snapshot(translation.get_target_instance())
            changes.update(PAGE, _key(page, translation.target_locale), before, after)

    return _in_savepoint(dry_run, work)


def override_image(
    page: Page,
    language_code: str,
    path: str,
    image: Any,
    *,
    user: AbstractBaseUser | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Replace one image of `page` in one language only, as a translator does in the editor.

    `path` names the image segment: `image` for the main image, `body.<block id>.image` for an
    image block. The other languages keep the source image. Raises LookupError when the page
    has no wagtail-localize translation in that language or no such image segment.
    """
    page = _current(page)
    translation = (
        Translation.objects.filter(
            source__object_id=page.translation_key,
            target_locale__language_code=language_code,
            enabled=True,
        )
        .select_related("source", "target_locale")
        .first()
    )
    if translation is None:
        raise LookupError(f"No translation of {page} in {language_code!r}")
    segment = OverridableSegment.objects.filter(
        source=translation.source, context__path=path
    ).first()
    if segment is None:
        raise LookupError(f"No image at {path!r} in {page}")

    def work(changes: ChangeSet) -> None:
        SegmentOverride.objects.update_or_create(
            locale=translation.target_locale,
            context=segment.context,
            defaults={"data_json": json.dumps(image.pk), "last_translated_by": user},
        )
        target = translation.get_target_instance()
        before = _snapshot(target)
        translation.save_target(user=user, publish=target.live)
        after = _snapshot(translation.get_target_instance())
        changes.update(PAGE, _key(page, translation.target_locale), before, after)

    return _in_savepoint(dry_run, work)


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


# --- Customer legal and checkout pages per language (I18N.9, Q-42)

LEGAL_REVIEW_RECORDED = "cms.legal_page_review.recorded"


def legal_review_snapshot(review: LegalPageReview) -> dict[str, Any]:
    return {
        "page": review.version.page_id,
        "language_code": review.version.language_code,
        "version": review.version.number,
        "stage": review.stage,
        "reviewer": review.reviewer,
        "reviewed_on": review.reviewed_on.isoformat(),
        "note": review.note,
    }


def record_legal_review(
    version: LegalPageVersion,
    stage: str,
    *,
    reviewer: str,
    reviewed_on: dt.date,
    ctx: AuditContext,
    note: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Record that `stage` (AI draft, native-speaker review, legal counsel check) was done on
    one language's published text, by `reviewer` on `reviewed_on`.

    Each version of each language needs its own reviews: a new version is not covered by the
    reviews of the previous one. Raises ValidationError for an unknown stage, an empty
    reviewer, a date in the future or a stage already recorded on the version.
    """
    reviewer = reviewer.strip()
    if stage not in LegalPageReview.Stage.values:
        raise ValidationError({"stage": f"Unknown review stage {stage!r}."})
    if not reviewer:
        raise ValidationError({"reviewer": "Say who did the review."})
    if reviewed_on > timezone.localdate():
        raise ValidationError({"reviewed_on": "A review cannot be dated in the future."})
    if version.reviews.filter(stage=stage).exists():
        raise ValidationError({"stage": f"{stage} is already recorded on this version."})
    review = LegalPageReview(
        version=version,
        stage=stage,
        reviewer=reviewer,
        reviewed_on=reviewed_on,
        note=note,
        recorded_at=timezone.now(),
        recorded_by=ctx.actor_label,
    )
    changeset = ChangeSet()
    changeset.create(
        "cms.legalpagereview",
        f"{version.page_id}/{version.number}/{stage}",
        legal_review_snapshot(review),
    )
    return run_mutation(
        changeset, review.save, ctx=ctx, action=LEGAL_REVIEW_RECORDED, dry_run=dry_run
    )


LEGAL_DATE_SET = "cms.legal_page.effective_date_set"


def set_legal_effective_date(
    page: LegalPage,
    effective_date: dt.date,
    *,
    ctx: AuditContext,
    user: AbstractBaseUser | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Give one language's legal page its own effective date, keeping its wagtail-localize sync.

    The way a translator sets a translation's date (I18N.9): the localize editor cannot show the
    field. The page's live text is saved with the new date as a revision, published when the
    page is live (which records the version, `record_legal_version`). Audited. Raises
    ValidationError for a date before the source text it translates or before its own last
    version. An unchanged date changes nothing.
    """
    page = LegalPage.objects.select_related("locale").get(pk=page.pk)  # the live text
    latest = page.versions.order_by("-number").first()
    problem = engine.translation_date_problem(
        effective_date,
        source=page.synced_source_date(),
        latest=latest and latest.effective_date,
    )
    if problem == "source":
        raise ValidationError(
            {"effective_date": "A translation cannot take effect before the text it translates."}
        )
    if problem == "latest":
        raise ValidationError(
            {"effective_date": "A new version cannot take effect before the previous one."}
        )
    changeset = ChangeSet()
    if effective_date == page.effective_date:
        return changeset
    key = f"{page.pk}"
    language = page.locale.language_code
    changeset.update(
        "cms.legalpage",
        key,
        {"language_code": language, "effective_date": page.effective_date.isoformat()},
        {"language_code": language, "effective_date": effective_date.isoformat()},
    )

    def apply() -> None:
        page.effective_date = effective_date
        revision = page.save_revision(user=user, log_action=True)
        if page.live:
            revision.publish(user=user)

    return run_mutation(changeset, apply, ctx=ctx, action=LEGAL_DATE_SET, dry_run=dry_run)


GoliveCheck = Callable[[Sequence[str] | None], list[str]]
# Checks other apps add to `golive_readiness` (one go-live gate): each takes the languages (None
# for all) and returns problem messages. `apps.compliance` registers the DSA and P2B check
# (CMP.5, D-27); cms cannot import it.
GOLIVE_CHECKS: list[GoliveCheck] = []


def register_golive_check(check: GoliveCheck) -> None:
    if check not in GOLIVE_CHECKS:
        GOLIVE_CHECKS.append(check)


def golive_problems(languages: Sequence[str] | None = None) -> list[str]:
    """Every go-live problem: the legal pages of all kinds with their reviews (I18N.9, D-27),
    then the registered checks."""
    kinds = (*engine.GO_LIVE_LEGAL_KINDS, *engine.DSA_LEGAL_KINDS)
    messages = [p.message for p in legal_readiness(languages, kinds=kinds)]
    for check in GOLIVE_CHECKS:
        messages.extend(check(languages))
    return messages


def legal_readiness(
    languages: Sequence[str] | None = None, kinds: Sequence[str] | None = None
) -> list[engine.ReadinessProblem]:
    """What stops go-live on the customer legal and checkout pages, in every go-live language
    (all of settings.LANGUAGES unless `languages` is given): a page without a published
    translation in a language, or a language's published text without its reviews."""
    kinds = kinds or engine.GO_LIVE_LEGAL_KINDS
    pages = (
        LegalPage.objects.live()
        .filter(kind__in=kinds, alias_of__isnull=True)
        .select_related("locale")
    )
    texts = []
    for page in pages:
        latest = page.versions.order_by("-number").first()
        stages = latest.reviews.values_list("stage", flat=True) if latest else []
        texts.append(
            engine.PublishedLegalText(
                kind=page.kind,
                language=page.locale.language_code,
                version=latest.number if latest else None,
                reviews=frozenset(stages),
            )
        )
    return engine.legal_readiness_problems(texts, kinds, languages or configured_languages())


def legal_section_tags(kinds: Iterable[str]) -> dict[tuple[str, str], set[str]]:
    """The required-section tags carried by the live legal pages of `kinds`, per (kind, language).

    A section counts when its text has content: markup alone (`<p></p>`) is empty. A page
    without any section still has its key, so callers tell "no page" from "no section".
    Read by `apps.compliance` (CMP.5), which owns the rules about which tags are required.
    """
    pages = (
        LegalPage.objects.live()
        .filter(kind__in=list(kinds), alias_of__isnull=True)
        .select_related("locale")
    )
    found: dict[tuple[str, str], set[str]] = {}
    for page in pages:
        tags = found.setdefault((page.kind, page.locale.language_code), set())
        tags.update(
            block.value["section"]
            for block in page.body
            if block.block_type == "legal_section" and strip_tags(str(block.value["text"])).strip()
        )
    return found


def _seed_legal_placeholders(home: HomePage, report: SeedReport) -> None:
    """Make sure the five go-live legal pages exist and are published in every language.

    Placeholders only: the texts are human work (Q-42). Each says it is not a legal text, and
    stays a draft on the site and in `legal_readiness` until a counsel check is recorded.
    Idempotent: a language that has the page, whatever its text, is left alone.
    """
    primary = home.locale
    today = timezone.localdate()
    for kind in engine.GO_LIVE_LEGAL_KINDS:
        source = LegalPage.objects.child_of(home).filter(kind=kind).first()
        if source is None:
            source = LegalPage(locale=primary, kind=kind, effective_date=today)
            _apply_text(source, legal_placeholder(primary.language_code, kind))
            home.add_child(instance=source)
            source = _publish(source)
            report.created.append(f"{primary.language_code} LegalPage {source.url_path}")
        for code in configured_languages():
            if code == primary.language_code:
                continue
            locale = Locale.objects.get(language_code=code)
            _translated(source, locale, legal_placeholder(code, kind), report)


def vendor_application_url(urlconf: str | None = None) -> str | None:
    """The VEN.22 application form's URL, or None while that route does not exist."""
    try:
        return reverse(APPLICATION_URL_NAME, urlconf=urlconf)
    except NoReverseMatch:
        return None


# --- Image credits disclosure (CMS.8, spec 10 §10.2)

# A block whose Meta sets `credit_in_disclosure = True` (CMS.2's hero, banners) shows its
# images without a caption area: they are credited in the page's "Image credits" disclosure.
DISCLOSURE_META = "credit_in_disclosure"
# A page type lists its own image fields shown without a caption area (a hero image field).
DISCLOSURE_FIELDS = "credit_disclosure_image_fields"


def _images_in(block: Any, value: Any, credited_here: bool) -> Iterable[Any]:
    credited_here = credited_here or bool(getattr(block.meta, DISCLOSURE_META, False))
    if value is None:
        return
    if isinstance(block, ImageChooserBlock):
        if credited_here:
            yield value
    elif isinstance(block, blocks.StructBlock):
        for name, child in block.child_blocks.items():
            yield from _images_in(child, value.get(name), credited_here)
    elif isinstance(block, blocks.ListBlock):
        for item in value:
            yield from _images_in(block.child_block, item, credited_here)
    elif isinstance(block, blocks.StreamBlock):
        for child in value:
            yield from _images_in(child.block, child.value, credited_here)


def disclosure_images_in_stream(stream: Iterable[Any]) -> Iterable[Any]:
    """The images of a StreamField value that are credited in the disclosure, in page order."""
    for child in stream:
        yield from _images_in(child.block, child.value, credited_here=False)


def page_image_credits(page: Page) -> list[Credit]:
    """The credits of the page's images shown without a caption area, once per image.

    Only images whose licence requires attribution have one; captioned images are credited
    in their `<figcaption>` instead.
    """
    # An unsaved preview (CMS.5) is already its specific class and has no content type yet.
    if type(page) is Page:
        page = page.specific
    images: list[Any] = [getattr(page, name, None) for name in getattr(page, DISCLOSURE_FIELDS, ())]
    for model_field in page._meta.get_fields():
        if isinstance(model_field, StreamField):
            images.extend(disclosure_images_in_stream(getattr(page, model_field.name)))
    credits: list[Credit] = []
    seen: set[int] = set()
    for image in images:
        if image is None or image.pk in seen:
            continue
        seen.add(image.pk)
        credit = image.credit
        if credit is not None:
            credits.append(credit)
    return credits


# --- Blog articles (CMS.3, spec 10 §10.2)


SCHEDULE_PROBLEMS = {
    engine.EXPIRY_IN_PAST: _("The expiry date must be in the future."),
    engine.EXPIRY_NOT_AFTER_GO_LIVE: _("The expiry date must be after the go-live date."),
}


@dataclass(frozen=True)
class ArticleData:
    """An article as a script or a migration (MIG.7) brings it: the legacy date and author name
    are kept. `body` is StreamField data, [(block type, value), ...], as the page takes it."""

    title: str
    slug: str
    date: dt.date
    body: list[tuple[str, Any]] = field(default_factory=list)
    summary: str = ""
    author: str = ""  # the byline; empty for none
    go_live_at: dt.datetime | None = None
    expire_at: dt.datetime | None = None
    seo_title: str = ""
    search_description: str = ""


def article_snapshot(post: BlogPostPage) -> dict[str, Any]:
    body = json.dumps(post.body.get_prep_value(), sort_keys=True, default=str)
    return {
        "title": post.title,
        "slug": post.slug,
        "language_code": post.locale.language_code,
        "date": post.date.isoformat(),
        "author": post.author.name if post.author else "",
        "summary": post.summary,
        "live": post.live,
        "go_live_at": post.go_live_at and post.go_live_at.isoformat(),
        "expire_at": post.expire_at and post.expire_at.isoformat(),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def _blog_author(name: str, locale: Locale, changes: ChangeSet) -> BlogAuthor:
    """The author `name` of `locale`. Missing there, it is made a translation of the same author
    in another language (bio and alt text copied until translated), or created with no bio."""
    author: BlogAuthor | None = BlogAuthor.objects.filter(name=name, locale=locale).first()
    if author is not None:
        return author
    source: BlogAuthor | None = BlogAuthor.objects.filter(name=name).order_by("pk").first()
    if source is not None:
        author = source.copy_for_translation(locale)
        author.save()
    else:
        author = BlogAuthor.objects.create(name=name, locale=locale)
    changes.create(
        "cms.blogauthor",
        f"{locale.language_code}:{author.slug}",
        {"name": author.name, "slug": author.slug, "language_code": locale.language_code},
    )
    return author


def import_article(
    index: BlogIndexPage,
    article: ArticleData,
    *,
    user: AbstractBaseUser | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Create `article` under the blog `index` (so in its language) and publish it.

    With a go-live in the future the article is scheduled, not live, as when an editor
    schedules it; the `cms.publish_scheduled` job publishes it on time. The author is the
    `BlogAuthor` of that name in the blog's language: missing there, it becomes a translation
    of the same author in another language, or a new author without bio or photo, which editors
    add in the admin. An article whose slug the blog already has
    is left alone (an empty ChangeSet), so a migration can run again. Raises ValidationError
    for an invalid schedule or page.

    Wagtail logs the page's creation and publication in its own history, as for an editor.
    """
    problem = engine.schedule_problem(article.go_live_at, article.expire_at, timezone.now())
    if problem is not None:
        raise ValidationError({"expire_at": SCHEDULE_PROBLEMS[problem]})
    index = _current(index)
    if index.get_children().filter(slug=article.slug).exists():
        return ChangeSet()

    def work(changes: ChangeSet) -> None:
        author = _blog_author(article.author, index.locale, changes) if article.author else None
        post = BlogPostPage(
            title=article.title,
            slug=article.slug,
            date=article.date,
            author=author,
            summary=article.summary,
            body=article.body,
            go_live_at=article.go_live_at,
            expire_at=article.expire_at,
            seo_title=article.seo_title,
            search_description=article.search_description,
            live=False,
        )
        index.add_child(instance=post)
        post.save_revision(user=user, log_action=True).publish(user=user)
        post = _current(post)
        changes.create(
            "cms.blogpostpage",
            f"{post.locale.language_code}:{post.slug}",
            article_snapshot(post),
        )

    return _in_savepoint(dry_run, work)


# --- Term and vendor landing pages (CMS.4, spec 10 §10.2)
#
# A term landing page shows on its term's listing page (D-07, CAT.8): the catalog view asks the
# `listings.EDITORIAL_HOOK` Wagtail hook (registered in `wagtail_hooks`) for it, so catalog never
# imports cms. Products come from the CAT.8 listing query: published, of active vendors.


@dataclass(frozen=True)
class CategorySection:
    """The term's newest products in one top-level category, and the term listing narrowed to
    that category (its canonical filtered URL)."""

    name: str
    cards: list[catalog.ProductCard]
    see_all_url: str


def term_listing_path(term: Term, language: str) -> str | None:
    """The path of the term's page in `language` (its current URL, D-07); None without one."""
    anchor = catalog.scope_anchor(catalog.ListingScope(term=term), language)
    return anchor.paths.get(language) if anchor is not None else None


def term_category_sections(
    term: Term, language: str, limit: int, term_path: str
) -> list[CategorySection]:
    """The term's products by top-level category of their main category, in tree order, up to
    `limit` each. None for a single category (`engine.shows_category_sections`). A query for the
    categories, then a bounded number per category."""
    limit = engine.landing_category_limit(limit)
    if limit == 0:
        return []
    listed = catalog.listing_products(catalog.ListingScope(term=term)).order_by()
    paths = listed.exclude(category=None).values_list("category__path", flat=True).distinct()
    roots = {path[: Category.steplen] for path in paths}
    prefix = slugs.language_prefix(language, default_language=settings.LANGUAGE_CODE)
    sections: list[CategorySection] = []
    for category in Category.objects.filter(path__in=roots).order_by("path"):
        anchor = catalog.scope_anchor(catalog.ListingScope(category=category), language)
        path = anchor.paths.get(language) if anchor is not None else None
        value = engine.listing_slug_path(path, prefix) if path else None
        if anchor is None or value is None:
            continue  # no public slug in this language: no filter link to build
        grid = catalog.product_grid(
            catalog.ListingScope(term=term, category=category),
            language,
            limit=limit,
            with_anchor=False,
        )
        if grid.cards:
            see_all = listings.listing_url(term_path, listings.ListingParams(category=value))
            sections.append(CategorySection(anchor.name, grid.cards, see_all))
    return sections if engine.shows_category_sections(len(sections), limit) else []


def _term_landing_parts(page: Any, request: HttpRequest | None, term_path: str) -> dict[str, Any]:
    language = page.locale.language_code
    return {
        "page": page,
        "request": request,
        "currency": CURRENCY,
        "sections": term_category_sections(page.term, language, page.category_products, term_path),
        "related": public_cards([child.value for child in page.related], language, request),
    }


def term_landing_preview(page: Any, request: HttpRequest | None) -> dict[str, Any]:
    """What the editor's preview shows: the editorial part, the term's name and its newest
    products (the listing's first page)."""
    if page.term is None:
        return {"name": page.title, "cards": [], "sections": [], "related": []}
    language = page.locale.language_code
    term_path = term_listing_path(page.term, language) or ""
    grid = catalog.product_grid(
        catalog.ListingScope(term=page.term), language, limit=catalog.LISTING_PER_PAGE
    )
    text = page.term.translation(language).translation
    return {
        **_term_landing_parts(page, request, term_path),
        "name": str(text.name) if text is not None else page.title,
        "cards": grid.cards,
    }


def _term_editorial(
    request: HttpRequest, term: Term, language: str
) -> listings.ListingEditorial | None:
    page = (
        TermLandingPage.objects.live()
        .filter(term=term, locale__language_code=language)
        .select_related("term", "locale")
        .first()
    )
    term_path = term_listing_path(term, language) if page is not None else None
    if page is None or term_path is None:
        return None
    context = _term_landing_parts(page, request, term_path)
    return listings.ListingEditorial(
        before=render_to_string("cms/includes/term_landing_before.html", context, request),
        after=render_to_string("cms/includes/term_landing_after.html", context, request),
        title=page.seo_title,
        description=page.search_description,
    )


def _vendor_story(
    request: HttpRequest, vendor: Vendor, language: str
) -> listings.ListingEditorial | None:
    page = (
        VendorLandingPage.objects.live()
        .for_vendor(vendor)
        .filter(locale__language_code=language)
        .select_related("vendor")
        .first()
    )
    url = page.get_url(request) if page is not None else None
    if not url:
        return None
    context = {"url": url, "vendor_name": vendor.display_name}
    return listings.ListingEditorial(
        before=render_to_string("cms/includes/vendor_story_link.html", context, request)
    )


def listing_editorial(
    request: HttpRequest, scope: catalog.ListingScope, language: str
) -> listings.ListingEditorial | None:
    """The editorial content of a listing's first page (the catalog's `EDITORIAL_HOOK`): a term
    page's landing page, or the link from a vendor storefront to the vendor's story."""
    if scope.term is not None:
        return _term_editorial(request, scope.term, language)
    if scope.vendor is not None:
        return _vendor_story(request, scope.vendor, language)
    return None


def page_language_urls(page: Page | None, request: HttpRequest | None) -> dict[str, str]:
    """{language code: URL} of the live translations of `page` (itself included): the one
    source of the page's `hreflang` links and of its language switcher (`apps.core.i18n`)."""
    if page is None:
        return {}
    urls: dict[str, str] = {}
    for translated in page.get_translations(inclusive=True).live().select_related("locale"):
        # A page that is live but not shown to visitors (a hidden event) is not offered.
        if not getattr(translated.specific, "is_publicly_visible", lambda: True)():
            continue
        url = translated.get_url(request)
        if url is not None:
            urls[translated.locale.language_code] = url
    return urls


def vendor_landing_context(page: Any, request: HttpRequest | None) -> dict[str, Any]:
    """The vendor's featured products (its storefront listing's newest) and the page's own
    canonical and `hreflang` links: its live translations of a public vendor."""
    language = page.locale.language_code
    grid = None
    if page.is_public() and page.featured_products > 0:
        grid = catalog.product_grid(
            catalog.ListingScope(vendor=page.vendor),
            language,
            limit=engine.grid_limit(page.featured_products),
        )

    def absolute(url: str | None) -> str | None:
        if not url:
            return None
        return request.build_absolute_uri(url) if request is not None else url

    found: dict[str, str] = {}
    for code, url in page_language_urls(page, request).items():
        absolute_url = absolute(url)
        if absolute_url is not None:
            found[code] = absolute_url
    alternates = hreflang_alternates(found)
    return {
        "featured": grid,
        "currency": CURRENCY,
        "canonical_url": absolute(page.get_url(request)),
        "alternates": alternates,
    }


# --- Rich-text sanitiser (CMS.12)


def _sanitise_stream_child(block: blocks.StreamBlock, child: Any) -> Any:
    if isinstance(child, dict) and child.get("type") in block.child_blocks:
        inner = _sanitise_block_value(block.child_blocks[child["type"]], child.get("value"))
        return {**child, "value": inner}
    return child


def _sanitise_block_value(block: blocks.Block, value: Any) -> Any:
    """`value` (a block's stored form) with every rich text inside cleaned."""
    if isinstance(block, blocks.RichTextBlock):
        if isinstance(value, str):
            return engine.sanitise_rich_text(value, block.features)
        return value
    if isinstance(block, blocks.StructBlock) and isinstance(value, dict):
        return {
            name: _sanitise_block_value(block.child_blocks[name], item)
            if name in block.child_blocks
            else item
            for name, item in value.items()
        }
    if isinstance(block, blocks.StreamBlock) and isinstance(value, list):
        return [_sanitise_stream_child(block, child) for child in value]
    if isinstance(block, blocks.ListBlock) and isinstance(value, list):
        return [
            {**item, "value": _sanitise_block_value(block.child_block, item["value"])}
            if isinstance(item, dict) and item.get("type") == "item" and "value" in item
            else _sanitise_block_value(block.child_block, item)
            for item in value
        ]
    return value


def sanitise_rich_text_fields(instance: Any, only: Collection[str] | None = None) -> list[str]:
    """Clean every rich text of a model instance in place: `RichTextField`s and the
    `RichTextBlock`s of its StreamFields (CMS.12). Returns the names of the fields changed.

    Called before every save, so import, seed, API and migration writes are covered; the
    editor's own output is already clean and stays byte for byte. `only` limits the work to
    those field names (`save(update_fields=...)` writes just those). Writes that skip
    `pre_save` (`bulk_create`, `bulk_update`, `QuerySet.update` of a rich-text field, `loaddata`)
    must call this first: see docs/dev/rich-text.md.
    """
    changed: list[str] = []
    for model_field in instance._meta.get_fields():
        if only is not None and model_field.name not in only:
            continue
        if isinstance(model_field, RichTextField):
            current = getattr(instance, model_field.attname)
            clean = engine.sanitise_rich_text(current, model_field.features)
            if clean != current:
                setattr(instance, model_field.attname, clean)
                changed.append(model_field.name)
        elif isinstance(model_field, StreamField):
            value = getattr(instance, model_field.attname)
            if value is None:
                continue
            stream_block = model_field.stream_block
            stored = stream_block.get_prep_value(value)
            clean_stored = _sanitise_block_value(stream_block, stored)
            if clean_stored != stored:
                setattr(instance, model_field.attname, stream_block.to_python(clean_stored))
                changed.append(model_field.name)
    return changed
