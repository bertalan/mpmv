"""Wagtail page types and the per-language navigation (CMS.1, spec 10 §10.2).

Each language has its own page tree under the Wagtail root: one `HomePage` per locale, linked as
translations (same `translation_key`), with the site's pages below it. `/` serves the EN home,
`/it/` the IT one, and so on (I18N.1). Navigation menus are translatable snippets: one header
and one footer menu per locale, falling back to the primary language when a locale has none.
Their links are a StreamField (page or external link): unlike inline panels and FK choosers,
its admin editor needs no inline script, so it works under the strict CSP (SEC.1).

Bodies are StreamFields with rich-text paragraphs, images and galleries (I18N.3), heroes, calls to
action, FAQs and "featured links" cards to pages, catalog terms and vendors (CMS.2); home and
standard pages also take launch countdowns (CMS.9) and the product grid (CMS.10). A page with a
running countdown caps the Cache-Control `max-age` and `s-maxage` set upstream at the target
(`CountdownCacheMixin`), so no cache serves the countdown after the launch; it never adds a
lifetime.

Translation (I18N.3, spec 09 §9.2): wagtail-localize copies a page into another locale and keeps
it in sync with its source. Texts, alt texts and captions are translated; images are synchronised
from the source, and a translator may override one for a single locale (D-18).

Legal pages (CMS.5) keep an append-only archive of every published text: one history per page,
and so per language, since a translation is its own page (Q-42). A translation keeps its own
effective date through wagtail-localize syncs, and the reviews of each language's text (AI draft,
native-speaker review, legal counsel check) are recorded on its versions (I18N.9). The "Sell
with us" page shows the plans and a FAQ and leads to the VEN.22 application form.

The blog (CMS.3): one `BlogIndexPage` per language under its home, with `BlogPostPage` articles.
An article keeps its own publication date (a migrated article keeps the legacy one), an optional
author (a `BlogAuthor` snippet: legacy authors are not platform users), a summary for the index,
a main image and a body whose featured links also take product cards. Scheduling is Wagtail's:
`go_live_at` and `expire_at` in the page settings, applied by the `cms.publish_scheduled` job.
An author has a name, a translated bio and a photo, and a page per language under the blog,
`author/<slug>/`, listing their articles (owner answer on PR #156).

Landing pages (CMS.4): a term landing page is the editorial part of a term's D-07 listing page
and lives on that URL; a vendor landing page tells an active vendor's story with its products.
"""

import datetime as dt
import uuid
from typing import Any, ClassVar
from urllib.parse import urljoin

from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.core.paginator import Page as PaginatorPage
from django.core.validators import MaxValueValidator, URLValidator
from django.db import models
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.cache import get_max_age, patch_cache_control
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.fields import RichTextField, StreamField
from wagtail.images import get_image_model_string
from wagtail.models import Page, PageManager, PageQuerySet, TranslatableMixin
from wagtail_localize.fields import BaseTranslatableField, SynchronizedField, TranslatableField
from wagtail_localize.models import Translation

from apps.vendors.scoping import VendorScopedManager, VendorScopedQuerySet
from apps.vendors.services import has_public_storefront

from . import engine
from .blocks import (
    RICH_TEXT_FEATURES,
    ArticleLinksBlock,
    CallToActionBlock,
    FaqBlock,
    FeaturedLinksBlock,
    FigureBlock,
    GalleryBlock,
    HeroBlock,
    LaunchCountdownBlock,
    LegalSectionBlock,
    PageCardBlock,
    ProductGridBlock,
    countdown_targets,
)
from .choosers import term_chooser, vendor_chooser


def body_field(
    verbose_name: Any = None,
    *,
    countdown: bool = False,
    products: bool = False,
    legal_sections: bool = False,
) -> StreamField:
    """A page body. `countdown`: also offer the launch countdown (CMS.9: home and standard
    pages; the page class then needs `CountdownCacheMixin`). `products`: also offer the product
    grid (CMS.10: home and standard pages)."""
    extra = [("countdown", LaunchCountdownBlock())] if countdown else []
    grid = [("product_grid", ProductGridBlock())] if products else []
    sections = [("legal_section", LegalSectionBlock())] if legal_sections else []
    return StreamField(
        [
            ("hero", HeroBlock()),
            ("paragraph", blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label=_("Rich text"))),
            ("figure", FigureBlock()),
            ("gallery", GalleryBlock()),
            ("cta", CallToActionBlock()),
            ("faq", FaqBlock()),
            ("featured", FeaturedLinksBlock()),
            *grid,
            *extra,
            *sections,
        ],
        blank=True,
        verbose_name=verbose_name or _("body"),
    )


class CountdownCacheMixin:
    """Caps the response's `max-age` and `s-maxage` at the nearest running countdown target
    of the page's body (CMS.9, spec 10 §10.2): a cache, the SEO.6 page cache included, never
    serves an active countdown after its launch. The cap only shortens a lifetime already set
    upstream: a response without `max-age` (or `s-maxage`) gets none, so a page that was not
    cacheable never becomes so. When it caps, a signed-in visitor's page is marked `private` too.
    A response that must not be stored (`no-store`) is left alone."""

    body: Any

    def serve(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        response: HttpResponseBase = super().serve(request, *args, **kwargs)  # type: ignore[misc]
        if "no-store" in response.get("Cache-Control", ""):
            return response
        current = {"max_age": get_max_age(response), "s_maxage": _directive(response, "s-maxage")}
        if all(value is None for value in current.values()):
            return response
        targets = countdown_targets(self.body)
        if not targets:
            return response
        now = timezone.now()
        directives: dict[str, Any] = {
            name: engine.capped_max_age(targets, now, value)
            for name, value in current.items()
            if value is not None
        }
        if request.user.is_authenticated:
            directives["private"] = True
        patch_cache_control(response, **directives)
        return response


def _directive(response: HttpResponseBase, name: str) -> int | None:
    for part in response.get("Cache-Control", "").split(","):
        key, _sep, value = part.strip().partition("=")
        if key.lower() == name and value.isdigit():
            return int(value)
    return None


class HomePage(CountdownCacheMixin, Page):  # type: ignore[misc]  # Wagtail is untyped
    """The root page of one language: `/` for EN, `/<code>/` for the others.

    One per locale: a second one would never be served. Checked in `clean()`, which Wagtail runs
    on every save (the page table holds the locale, so no database constraint can).
    """

    body = body_field(countdown=True, products=True)

    content_panels: ClassVar[list[Any]] = [*Page.content_panels, FieldPanel("body")]
    parent_page_types: ClassVar[list[str]] = ["wagtailcore.Page"]
    subpage_types: ClassVar[list[str]] = [
        "cms.StandardPage",
        "cms.LegalPage",
        "cms.VendorRecruitmentPage",
        "cms.BlogIndexPage",
        "cms.TermLandingPage",
        "cms.VendorLandingPage",
        # The `/events/` index (EVT.1): named by string, so cms never imports apps.events.
        "events.EventIndexPage",
    ]
    template = "cms/home_page.html"

    class Meta:
        verbose_name = _("home page")

    def clean(self) -> None:
        super().clean()
        others = HomePage.objects.filter(locale_id=self.locale_id).exclude(pk=self.pk)
        if self.locale_id is not None and others.exists():
            raise ValidationError(_("This language already has a home page."))


class StandardPage(CountdownCacheMixin, Page):  # type: ignore[misc]
    """A static page: about us, contact, FAQ (spec 10 §10.2), with an optional main image."""

    image = models.ForeignKey(
        get_image_model_string(),
        verbose_name=_("main image"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    image_alt_text = models.CharField(
        _("alt text"),
        max_length=250,
        blank=True,
        help_text=_("Describes the main image for people who cannot see it."),
    )
    image_caption = models.CharField(_("caption"), max_length=250, blank=True)
    body = body_field(countdown=True, products=True)

    content_panels: ClassVar[list[Any]] = [
        *Page.content_panels,
        FieldPanel("image"),
        FieldPanel("image_alt_text"),
        FieldPanel("image_caption"),
        FieldPanel("body"),
    ]
    # Spec 10 "Images per locale": the source image until a translator replaces it.
    override_translatable_fields: ClassVar[list[Any]] = [
        SynchronizedField("image", overridable=True)
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage", "cms.StandardPage"]
    subpage_types: ClassVar[list[str]] = [
        "cms.StandardPage",
        "cms.TermLandingPage",
        "cms.VendorLandingPage",
    ]
    template = "cms/standard_page.html"

    class Meta:
        verbose_name = _("standard page")

    def clean(self) -> None:
        super().clean()
        if self.image_id is not None and not self.image_alt_text.strip():
            raise ValidationError({"image_alt_text": _("Describe the main image.")})


class PageLinkBlock(blocks.StructBlock):  # type: ignore[misc]
    page = blocks.PageChooserBlock()
    label = blocks.CharBlock(
        required=False, max_length=100, help_text=_("Defaults to the page title.")
    )

    class Meta:
        icon = "doc-empty"
        label = _("Page")


class ExternalLinkBlock(blocks.StructBlock):  # type: ignore[misc]
    url = blocks.URLBlock()
    label = blocks.CharBlock(max_length=100)

    class Meta:
        icon = "link"
        label = _("External link")


class EditorRoleSeed(models.Model):
    """Marks an editor role (`apps.cms.roles`) as seeded (CORE.8): its permissions and page
    grants are written once, so a platform owner's later narrowing is never reverted by a
    deploy. Deleting the row (and nothing else) makes the next migrate seed the role again."""

    role = models.CharField(max_length=100, unique=True)
    seeded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.role)


class NavigationMenu(TranslatableMixin, models.Model):  # type: ignore[misc]
    """The header or footer links of one language (a snippet editors manage per locale)."""

    class Location(models.TextChoices):
        HEADER = "header", _("Header")
        FOOTER = "footer", _("Footer")

    location = models.CharField(_("location"), max_length=16, choices=Location.choices)
    links = StreamField(
        [("page", PageLinkBlock()), ("external", ExternalLinkBlock())],
        blank=True,
        verbose_name=_("links"),
    )

    panels: ClassVar[list[Any]] = [FieldPanel("location"), FieldPanel("links")]

    class Meta(TranslatableMixin.Meta):  # type: ignore[misc]
        verbose_name = _("navigation menu")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["locale", "location"], name="cms_one_menu_per_location_and_locale"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_location_display()} ({self.locale})"


# --- Legal pages (CMS.5)


def stream_data(stream: Any) -> list[list[Any]]:
    """A StreamField value as [[block type, value], ...], without the block ids."""
    return [[block["type"], block["value"]] for block in stream.get_prep_value()]


class LanguageOwnField(BaseTranslatableField):  # type: ignore[misc]  # untyped
    """A field each translation keeps for itself: wagtail-localize neither copies it from the
    source page on a sync nor offers it as a segment. A new translation starts from the
    source's value (Wagtail's copy). The wagtail-localize editor cannot show it, so the
    translator sets it with `services.set_legal_effective_date` (`manage.py
    set_legal_effective_date`) and the sync stays on."""

    def __repr__(self) -> str:
        return f"<LanguageOwnField {self.field_name}>"


class LegalPage(RoutablePageMixin, Page):  # type: ignore[misc]
    """Terms, privacy, cookies, withdrawal, shipping, returns: a text with its effective date.

    Every publish that changes the title, the text or the effective date records a
    `LegalPageVersion` (`services.record_legal_version`, on Wagtail's `page_published`). A
    translation is its own page, so each language has its own versions and dates (Q-42): a
    wagtail-localize sync brings the source's text, not its effective date (I18N.9), except
    that a text newer than the translation's date takes effect from the source's date
    (`engine.synced_translation_date`, applied in `clean`, which every save runs). The archive
    is served at `versions/<number>/`.

    No EN fallback (Q-42): a language without its own published page has no such page, so the
    URL is not found; nothing serves the EN text in its place (`language_fallback`).
    """

    class Kind(models.TextChoices):
        TERMS = "terms", _("Terms of sale")
        PRIVACY = "privacy", _("Privacy policy")
        COOKIES = "cookies", _("Cookie policy")
        WITHDRAWAL = "withdrawal", _("Right of withdrawal")
        SHIPPING = "shipping", _("Shipping")
        RETURNS = "returns", _("Returns")
        # CMP.5 (DSA article 14, P2B article 5): the platform's own terms of use, with the
        # restrictions and the moderation policy, and the ranking parameters page.
        PLATFORM_TERMS = "platform_terms", _("Platform terms of use")
        RANKING = "ranking", _("Ranking parameters")

    # Read by the visitor-content fallback (spec 09 §9.2) when it comes: legal pages opt out.
    language_fallback: ClassVar[bool] = False

    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices)
    effective_date = models.DateField(
        _("effective date"),
        help_text=_("Applies from this date; not before the last version's."),
    )
    body = body_field(legal_sections=True)

    content_panels: ClassVar[list[Any]] = [
        *Page.content_panels,
        FieldPanel("kind"),
        FieldPanel("effective_date"),
        FieldPanel("body"),
    ]
    override_translatable_fields: ClassVar[list[BaseTranslatableField]] = [
        LanguageOwnField("effective_date")
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage"]
    subpage_types: ClassVar[list[str]] = []
    template = "cms/legal_page.html"

    class Meta:
        verbose_name = _("legal page")

    def clean(self) -> None:
        super().clean()
        if self.pk is None or self.effective_date is None:
            return
        latest = self.versions.order_by("-number").first()
        # A sync never publishes a newer source text under the translation's older date.
        self.effective_date = engine.synced_translation_date(
            own=self.effective_date,
            source=self.synced_source_date(),
            text_changed=latest is None or self.text() != latest.text(),
        )
        if engine.is_backdated(latest and latest.effective_date, self.effective_date):
            raise ValidationError(
                {
                    "effective_date": _(
                        "A new version cannot take effect before the previous one (%(date)s)."
                    )
                    % {"date": latest.effective_date.isoformat()}
                }
            )

    def content(self) -> dict[str, Any]:
        """What a version records: compared with the last version on every publish."""
        return {**self.text(), "effective_date": self.effective_date.isoformat()}

    def text(self) -> dict[str, Any]:
        return {"title": self.title, "body": stream_data(self.body)}

    def synced_source_date(self) -> dt.date | None:
        """The effective date of the source text this page last synced from, when it is a
        wagtail-localize translation with the sync on (None otherwise)."""
        translation = (
            Translation.objects.filter(
                source__object_id=self.translation_key,
                target_locale_id=self.locale_id,
                enabled=True,
            )
            .select_related("source")
            .first()
        )
        if translation is None:
            return None
        source: dt.date | None = translation.source.as_instance().effective_date
        return source

    def version_url(self, number: int, request: HttpRequest | None = None) -> str:
        base: str = self.get_url(request) or ""
        sub: str = self.reverse_subpage("archived_version", args=[number])
        return base + sub

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        # An unsaved page (the editor's preview before the first save) has no versions yet.
        versions = list(self.versions.order_by("-number")) if self.pk else []
        stamps = [engine.VersionStamp(v.number, v.effective_date) for v in versions]
        in_force = engine.version_in_force(stamps, timezone.localdate())
        context["current_version"] = versions[0] if versions else None
        # A text is a draft until legal counsel has checked it (I18N.9), placeholders included.
        context["counsel_checked"] = bool(versions) and (
            versions[0].reviews.filter(stage=LegalPageReview.Stage.COUNSEL_CHECK).exists()
        )
        context["in_force"] = in_force
        context["in_force_url"] = in_force and self.version_url(in_force.number, request)
        context["archive"] = [(v, self.version_url(v.number, request)) for v in versions[1:]]
        return context

    @path("versions/<int:number>/", name="archived_version")  # type: ignore[untyped-decorator]
    def archived_version(self, request: HttpRequest, number: int) -> HttpResponse:
        version = get_object_or_404(self.versions, number=number)
        response: HttpResponse = self.render(
            request,
            template="cms/legal_page_version.html",
            context_overrides={"version": version},
        )
        return response


class LegalPageVersion(models.Model):
    """One published text of a legal page in one language. Append-only (DB trigger + guard).

    The page is protected: a legal page with versions is unpublished, never deleted.
    """

    page = models.ForeignKey(
        LegalPage, on_delete=models.PROTECT, related_name="versions", verbose_name=_("page")
    )
    number = models.PositiveIntegerField(_("number"))
    language_code = models.CharField(_("language"), max_length=16)
    title = models.CharField(_("title"), max_length=255)
    effective_date = models.DateField(_("effective date"))
    body = body_field(legal_sections=True)
    published_at = models.DateTimeField(_("published at"))
    published_by = models.CharField(_("published by"), max_length=150)

    class Meta:
        verbose_name = _("legal page version")
        ordering: ClassVar[list[str]] = ["page", "number"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["page", "number"], name="cms_legal_version_number"),
        ]

    def __str__(self) -> str:
        return f"{self.title} v{self.number} ({self.language_code})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("LegalPageVersion is append-only")
        super().save(*args, **kwargs)

    def content(self) -> dict[str, Any]:
        return {**self.text(), "effective_date": self.effective_date.isoformat()}

    def text(self) -> dict[str, Any]:
        return {"title": self.title, "body": stream_data(self.body)}


class LegalPageReview(models.Model):
    """A review of one language's published text (I18N.9, Q-42): the AI draft it started from,
    the native-speaker review or the legal counsel check, with who did it and when.

    Recorded on the version reviewed: a new version needs its own reviews. Append-only (DB
    trigger + guard), like the versions; written by `services.record_legal_review`, audited.
    """

    class Stage(models.TextChoices):
        AI_DRAFT = "ai_draft", _("AI draft")
        NATIVE_REVIEW = "native_review", _("Native-speaker review")
        COUNSEL_CHECK = "counsel_check", _("Legal counsel check")

    version = models.ForeignKey(
        LegalPageVersion,
        on_delete=models.PROTECT,
        related_name="reviews",
        verbose_name=_("version"),
    )
    stage = models.CharField(_("stage"), max_length=16, choices=Stage.choices)
    reviewer = models.CharField(
        _("reviewer"), max_length=150, help_text=_("Who did it: a person, a firm or a tool.")
    )
    reviewed_on = models.DateField(_("reviewed on"))
    note = models.TextField(_("note"), blank=True)
    recorded_at = models.DateTimeField(_("recorded at"))
    recorded_by = models.CharField(_("recorded by"), max_length=150)

    class Meta:
        verbose_name = _("legal page review")
        ordering: ClassVar[list[str]] = ["version", "stage"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["version", "stage"], name="cms_legal_review_stage"),
        ]

    def __str__(self) -> str:
        return f"{self.get_stage_display()} of {self.version}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("LegalPageReview is append-only")
        super().save(*args, **kwargs)


# --- Vendor recruitment (CMS.5)


class PlanBlock(blocks.StructBlock):  # type: ignore[misc]
    """A plan as shown to prospective vendors. The plans themselves are VEN.2 `VendorPlan`."""

    name = blocks.CharBlock(max_length=80)
    price = blocks.CharBlock(
        required=False, max_length=80, help_text=_("How the fee reads, e.g. 'No monthly fee'.")
    )
    summary = blocks.TextBlock(required=False, max_length=300)
    features = blocks.ListBlock(blocks.CharBlock(max_length=120), default=[])
    highlighted = blocks.BooleanBlock(required=False)

    class Meta:
        icon = "tag"
        label = _("Plan")


class FAQItemBlock(blocks.StructBlock):  # type: ignore[misc]
    question = blocks.CharBlock(max_length=200)
    answer = blocks.RichTextBlock(features=RICH_TEXT_FEATURES)

    class Meta:
        icon = "help"
        label = _("Question")


class VendorRecruitmentPage(Page):  # type: ignore[misc]
    """ "Sell with us": the plans, a FAQ and the call to apply (VEN.22's public form)."""

    intro = body_field(_("introduction"))
    plans = StreamField([("plan", PlanBlock())], blank=True, verbose_name=_("plans"))
    faq = StreamField([("question", FAQItemBlock())], blank=True, verbose_name=_("FAQ"))
    cta_label = models.CharField(
        _("call to action"), max_length=80, blank=True, help_text=_("Defaults to 'Apply now'.")
    )

    content_panels: ClassVar[list[Any]] = [
        *Page.content_panels,
        FieldPanel("intro"),
        FieldPanel("plans"),
        FieldPanel("faq"),
        FieldPanel("cta_label"),
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage"]
    subpage_types: ClassVar[list[str]] = []
    template = "cms/vendor_recruitment_page.html"

    class Meta:
        verbose_name = _("vendor recruitment page")

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from .services import vendor_application_url  # services import the models

        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        context["application_url"] = vendor_application_url()
        return context


# --- Blog (CMS.3)


AUTHOR_BIO_FEATURES = ["bold", "italic", "link"]
PROFILE_URL = URLValidator(schemes=["https", "http"])
AUTHOR_PHOTO_FILTER = "fill-240x240"  # the author page's photo (`blog_author_page.html`)
ARTICLE_IMAGE_FILTER = "width-1200"  # an article's main image (`blog_post_page.html`)


class BlogAuthor(TranslatableMixin, models.Model):  # type: ignore[misc]  # Wagtail is untyped
    """The byline of articles: a name, not a user account (legacy authors have none), with a
    bio, a photo and an author page. A translatable snippet: each language has its own copy, the
    bio and the photo's alt text are translated, the name, slug and photo are the source's."""

    name = models.CharField(_("name"), max_length=120)
    slug = models.SlugField(
        _("slug"),
        max_length=120,
        blank=True,
        help_text=_("The author page's address; made from the name when left empty."),
    )
    bio = RichTextField(_("bio"), features=AUTHOR_BIO_FEATURES, blank=True)
    photo = models.ForeignKey(
        get_image_model_string(),
        verbose_name=_("photo"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    photo_alt_text = models.CharField(
        _("photo alt text"),
        max_length=250,
        blank=True,
        help_text=_("Describes the photo for people who cannot see it."),
    )
    job_title = models.CharField(_("job title"), max_length=120, blank=True)
    same_as = StreamField(
        [("url", blocks.URLBlock(label=_("Profile URL"), validators=[PROFILE_URL]))],
        blank=True,
        verbose_name=_("profile links"),
        help_text=_("The author's profiles elsewhere (a social network, a personal site)."),
    )

    panels: ClassVar[list[Any]] = [
        FieldPanel("name"),
        FieldPanel("slug"),
        FieldPanel("job_title"),
        FieldPanel("bio"),
        FieldPanel("photo"),
        FieldPanel("photo_alt_text"),
        FieldPanel("same_as"),
    ]
    translatable_fields: ClassVar[list[Any]] = [
        SynchronizedField("name"),
        SynchronizedField("slug"),
        TranslatableField("job_title"),
        TranslatableField("bio"),
        SynchronizedField("photo"),
        TranslatableField("photo_alt_text"),
        SynchronizedField("same_as"),
    ]

    class Meta(TranslatableMixin.Meta):  # type: ignore[misc]
        verbose_name = _("blog author")
        verbose_name_plural = _("blog authors")
        ordering: ClassVar[list[str]] = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["locale", "name"], name="cms_blog_author_name_per_locale"
            ),
            models.UniqueConstraint(
                fields=["locale", "slug"], name="cms_blog_author_slug_per_locale"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.photo_id is not None and not self.photo_alt_text.strip():
            raise ValidationError({"photo_alt_text": _("Describe the photo.")})

    def translated(self, field: str, locale_id: int) -> str:
        """`field` (the bio or the job title) as shown in the language `locale_id`: empty when
        this copy is of another language or the text is still the original's (a translation
        copy keeps the source text until translated), never another language's text."""
        value: str = getattr(self, field)
        if not value or self.locale_id != locale_id:
            return ""
        original: BlogAuthor | None = (
            BlogAuthor.objects.filter(translation_key=self.translation_key).order_by("pk").first()
        )
        if original is not None and original.pk != self.pk and getattr(original, field) == value:
            return ""
        return value

    def profile_urls(self) -> list[str]:
        return [str(block.value) for block in self.same_as]


class BlogIndexPage(RoutablePageMixin, Page):  # type: ignore[misc]
    """`/blog/` of one language: its live articles, newest first, a page of them at a time.

    `author/<slug>/` is an author's page in this language: their bio and photo and their live
    articles here, paginated the same way. An author without articles gets an empty list.
    """

    PER_PAGE = 12

    intro = RichTextField(_("introduction"), features=RICH_TEXT_FEATURES, blank=True)

    content_panels: ClassVar[list[Any]] = [*Page.content_panels, FieldPanel("intro")]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage"]
    subpage_types: ClassVar[list[str]] = ["cms.BlogPostPage"]
    max_count_per_parent = 1
    template = "cms/blog_index_page.html"

    class Meta:
        verbose_name = _("blog index")

    def posts(self) -> Any:
        return (
            BlogPostPage.objects.child_of(self)
            .live()
            .public()
            .select_related("author")
            .order_by("-date", "-first_published_at", "-pk")
        )

    def author_named(self, slug: str) -> BlogAuthor:
        """The author of this language with `slug`; else another language's (an article may
        name an author not translated yet). Raises Http404 for none."""
        authors = BlogAuthor.objects.filter(slug=slug).select_related("photo")
        author: BlogAuthor | None = (
            authors.filter(locale_id=self.locale_id).first() or authors.order_by("pk").first()
        )
        if author is None:
            raise Http404
        return author

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        author: BlogAuthor | None = kwargs.get("author")
        queryset = self.posts()
        if author is not None:
            # Any language's copy of the author: the articles point at the copy they were given.
            queryset = queryset.filter(author__translation_key=author.translation_key)
        context["author"] = author
        if author is not None:
            context["author_bio"] = author.translated("bio", self.locale_id)
            context["author_job_title"] = author.translated("job_title", self.locale_id)
            context["jsonld"] = engine.profile_page_jsonld(
                url=self.author_url(author, request),
                person=self.person(author, request),
                language=self.locale.language_code,
            )
        paginator = Paginator(queryset, self.PER_PAGE)
        try:
            posts: PaginatorPage[Any] = paginator.page(request.GET.get("page", 1))
        except (EmptyPage, PageNotAnInteger) as error:
            raise Http404 from error
        context["posts"] = posts
        context["previous_url"] = (
            f"?page={posts.previous_page_number()}" if posts.has_previous() else ""
        )
        context["next_url"] = f"?page={posts.next_page_number()}" if posts.has_next() else ""
        context["blog"] = self
        return context

    def author_url(self, author: BlogAuthor, request: HttpRequest | None) -> str:
        """The absolute URL of `author`'s page on this blog."""
        base = self.get_full_url(request) or ""
        return f"{base}{self.reverse_subpage('author', args=[author.slug])}"

    def person(self, author: BlogAuthor, request: HttpRequest | None) -> dict[str, Any]:
        """schema.org Person of `author` in this blog's language; its `@id` is the author page
        URL plus `#person`, the same on the author page and on every article."""
        url = self.author_url(author, request)
        image = ""
        if author.photo is not None:
            image = urljoin(url, author.photo.get_rendition(AUTHOR_PHOTO_FILTER).url)
        return engine.person_jsonld(
            person_id=f"{url}#person",
            name=author.name,
            url=url,
            image=image,
            description=engine.plain_text(author.translated("bio", self.locale_id)),
            same_as=author.profile_urls(),
            job_title=author.translated("job_title", self.locale_id),
        )

    @path("author/<slug:slug>/", name="author")  # type: ignore[untyped-decorator]
    def author_page(self, request: HttpRequest, slug: str) -> HttpResponse:
        response: HttpResponse = self.render(
            request, template="cms/blog_author_page.html", author=self.author_named(slug)
        )
        return response


class BlogPostPage(Page):  # type: ignore[misc]
    """An article: date, author, summary, main image and body (spec 10 §10.2)."""

    date = models.DateField(
        _("publication date"),
        default=timezone.localdate,
        help_text=_("Shown on the article; a migrated article keeps its original date."),
    )
    author = models.ForeignKey(
        BlogAuthor,
        verbose_name=_("author"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    summary = models.TextField(
        _("summary"), max_length=300, blank=True, help_text=_("Shown on the blog index.")
    )
    image = models.ForeignKey(
        get_image_model_string(),
        verbose_name=_("main image"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    image_alt_text = models.CharField(
        _("alt text"),
        max_length=250,
        blank=True,
        help_text=_("Describes the main image for people who cannot see it."),
    )
    image_caption = models.CharField(_("caption"), max_length=250, blank=True)
    body = StreamField(
        [
            ("paragraph", blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label=_("Rich text"))),
            ("figure", FigureBlock()),
            ("gallery", GalleryBlock()),
            ("cta", CallToActionBlock()),
            ("faq", FaqBlock()),
            ("featured", ArticleLinksBlock()),
        ],
        blank=True,
        verbose_name=_("body"),
    )

    content_panels: ClassVar[list[Any]] = [
        *Page.content_panels,
        FieldPanel("date"),
        FieldPanel("author"),
        FieldPanel("summary"),
        FieldPanel("image"),
        FieldPanel("image_alt_text"),
        FieldPanel("image_caption"),
        FieldPanel("body"),
    ]
    override_translatable_fields: ClassVar[list[Any]] = [
        SynchronizedField("image", overridable=True)
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.BlogIndexPage"]
    subpage_types: ClassVar[list[str]] = []
    template = "cms/blog_post_page.html"

    class Meta:
        verbose_name = _("blog article")

    def clean(self) -> None:
        super().clean()
        if self.image_id is not None and not self.image_alt_text.strip():
            raise ValidationError({"image_alt_text": _("Describe the main image.")})

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        parent = self.get_parent()
        blog = parent.specific if parent is not None else None
        context["blog"] = blog  # the byline's link
        url = self.get_full_url(request) or ""
        author_id = ""
        if self.author is not None and isinstance(blog, BlogIndexPage) and self.author.slug:
            author_id = f"{blog.author_url(self.author, request)}#person"
        image = ""
        if self.image is not None:
            image = urljoin(url, self.image.get_rendition(ARTICLE_IMAGE_FILTER).url)
        context["jsonld"] = engine.blog_posting_jsonld(
            url=url,
            headline=self.title,
            date_published=self.date,
            language=self.locale.language_code,
            author_id=author_id,
            author_name=self.author.name if self.author is not None else "",
            description=self.summary,
            image=image,
        )
        return context


# --- Term and vendor landing pages (CMS.4)


def _unique_in_locale(page: Page, model: type[Page], field: str, target_id: int) -> None:
    """One landing page per term (or vendor) and language: a second one would never show."""
    others = model.objects.filter(locale_id=page.locale_id, **{f"{field}_id": target_id})
    if page.locale_id is not None and others.exclude(pk=page.pk).exists():
        raise ValidationError({field: _("This language already has a landing page for it.")})


class TermLandingPage(Page):  # type: ignore[misc]
    """The editorial part of a taxonomy term's page (spec 10 §10.2).

    A term page lives on its D-07 listing URL (`/{taxonomy}/{term}/`, CAT.8), so this page adds
    no URL of its own: its intro, the term's products by category and its related articles show
    on the listing's first unfiltered page (`services.listing_editorial`), under the listing's
    canonical and hreflang. Its URL is the term URL in its language, which follows the term's
    current URL (MIG.11); its place in the page tree answers one 301 there. The editor's preview
    renders the editorial part with the term's newest products.
    """

    term = models.ForeignKey(
        "catalog.Term",
        verbose_name=_("term"),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_("A term of a taxonomy with term pages."),
    )
    body = body_field(_("introduction"))
    category_products = models.PositiveSmallIntegerField(
        _("products per category"),
        default=engine.LANDING_CATEGORY_DEFAULT,
        validators=[MaxValueValidator(engine.LANDING_CATEGORY_MAX)],
        help_text=_(
            "The term's newest products shown for each category; 0 shows no category sections."
        ),
    )
    related = StreamField(
        [("page", PageCardBlock())], blank=True, verbose_name=_("related articles")
    )

    content_panels: ClassVar[list[Any]] = [
        *Page.content_panels,
        FieldPanel("term", widget=term_chooser.widget_class),
        FieldPanel("body"),
        FieldPanel("category_products"),
        FieldPanel("related"),
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage", "cms.StandardPage"]
    subpage_types: ClassVar[list[str]] = []
    template = "cms/term_landing_page.html"

    class Meta:
        verbose_name = _("term landing page")

    def clean(self) -> None:
        super().clean()
        term = self.term
        if term is None:
            return  # the field's own "required" error
        if not term.taxonomy.has_landing:
            raise ValidationError({"term": _("This term's taxonomy has no term pages.")})
        _unique_in_locale(self, TermLandingPage, "term", term.pk)

    def listing_path(self) -> str | None:
        """The term page's path in this page's language; None when the term has none."""
        from .services import term_listing_path  # services import the models

        if self.term is None:
            return None
        return term_listing_path(self.term, self.locale.language_code)

    def get_url_parts(self, request: HttpRequest | None = None) -> Any:
        parts = super().get_url_parts(request)
        path = self.listing_path() if parts is not None else None
        if parts is None or path is None:
            return None
        site_id, root_url, _page_path = parts
        return site_id, root_url, path

    def get_sitemap_urls(self, request: HttpRequest | None = None) -> list[dict[str, Any]]:
        return []  # the term page is listed once, as a catalog listing

    def serve(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        path = self.listing_path()
        if path is None:
            raise Http404("This term has no page.")
        return HttpResponsePermanentRedirect(path)

    def serve_preview(self, request: HttpRequest, mode_name: str) -> HttpResponseBase:
        # As Wagtail's own serve_preview does; Django's HttpRequest does not declare them.
        request.is_preview = True  # type: ignore[attr-defined]
        request.preview_mode = mode_name  # type: ignore[attr-defined]
        response: HttpResponseBase = super().serve(request)  # render, never redirect
        return response

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from .services import term_landing_preview

        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        context.update(term_landing_preview(self, request))
        return context


class VendorLandingPageQuerySet(PageQuerySet, VendorScopedQuerySet["VendorLandingPage"]):  # type: ignore[misc]
    """Wagtail's page queryset with the CORE.3 vendor scoping."""


class VendorLandingPageManager(PageManager, VendorScopedManager["VendorLandingPage"]):  # type: ignore[misc]
    """A `PageManager` (Wagtail requires it) that is also the CORE.3 scoped manager."""

    _queryset_class = VendorLandingPageQuerySet


class VendorLandingPage(Page):  # type: ignore[misc]
    """A vendor's story beyond its storefront: heritage, workshop, images and its featured
    products (spec 10 §10.2), linked from the storefront. Shown only while the vendor is
    active: otherwise it has no URL and answers 404. Vendor-owned data (CORE.3 scoping).
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorLandingPageManager()

    # The id the vendor area and the API show (like BaseModel's); each translation has its own,
    # so a copy for translation gets a new one.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    vendor = models.ForeignKey(
        "vendors.Vendor",
        verbose_name=_("vendor"),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    body = body_field(_("story"), products=True)
    featured_products = models.PositiveSmallIntegerField(
        _("featured products"),
        default=engine.PRODUCT_GRID_DEFAULT,
        validators=[MaxValueValidator(engine.PRODUCT_GRID_MAX)],
        help_text=_("The vendor's newest products shown after the story; 0 shows none."),
    )

    content_panels: ClassVar[list[Any]] = [
        *Page.content_panels,
        FieldPanel("vendor", widget=vendor_chooser.widget_class),
        FieldPanel("body"),
        FieldPanel("featured_products"),
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage", "cms.StandardPage"]
    subpage_types: ClassVar[list[str]] = []
    exclude_fields_in_copy: ClassVar[list[str]] = ["public_id"]
    template = "cms/vendor_landing_page.html"

    class Meta:
        verbose_name = _("vendor landing page")

    def clean(self) -> None:
        super().clean()
        vendor = self.vendor
        if vendor is None:
            return  # the field's own "required" error
        if not has_public_storefront(vendor):
            raise ValidationError({"vendor": _("Choose an active vendor.")})
        _unique_in_locale(self, VendorLandingPage, "vendor", vendor.pk)

    def is_public(self) -> bool:
        return self.vendor is not None and has_public_storefront(self.vendor)

    def get_url_parts(self, request: HttpRequest | None = None) -> Any:
        return super().get_url_parts(request) if self.is_public() else None

    def serve(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not self.is_public():
            raise Http404("This vendor has no public page.")
        response: HttpResponseBase = super().serve(request, *args, **kwargs)
        return response

    def get_context(self, request: HttpRequest, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from .services import vendor_landing_context

        context: dict[str, Any] = super().get_context(request, *args, **kwargs)
        context.update(vendor_landing_context(self, request))
        return context
