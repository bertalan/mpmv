"""CMS services: locales from settings, the editors' MFA policy, seed pages and menus (CMS.1);
page translation with wagtail-localize (I18N.3); legal page versions and the link to the vendor
application (CMS.5); the page's image credits (CMS.8); the go-live readiness of the customer
legal and checkout pages, their per-language reviews and placeholders (I18N.9); blog articles
created by scripts and migrations (CMS.3).

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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.fields import StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Locale, Page, Site
from wagtail_localize.models import (
    OverridableSegment,
    SegmentOverride,
    Translation,
    TranslationSource,
)
from wagtail_localize.operations import TranslationCreator

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.core.image_licences import Credit
from apps.core.services import run_mutation

from . import engine
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


def legal_readiness(languages: Sequence[str] | None = None) -> list[engine.ReadinessProblem]:
    """What stops go-live on the customer legal and checkout pages, in every go-live language
    (all of settings.LANGUAGES unless `languages` is given): a page without a published
    translation in a language, or a language's published text without its reviews."""
    pages = (
        LegalPage.objects.live()
        .filter(kind__in=engine.GO_LIVE_LEGAL_KINDS, alias_of__isnull=True)
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
    return engine.legal_readiness_problems(
        texts, engine.GO_LIVE_LEGAL_KINDS, languages or configured_languages()
    )


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
