"""Wagtail page types and the per-language navigation (CMS.1, spec 10 §10.2).

Each language has its own page tree under the Wagtail root: one `HomePage` per locale, linked as
translations (same `translation_key`), with the site's pages below it. `/` serves the EN home,
`/it/` the IT one, and so on (I18N.1). Navigation menus are translatable snippets: one header
and one footer menu per locale, falling back to the primary language when a locale has none.
Their links are a StreamField (page or external link): unlike inline panels and FK choosers,
its admin editor needs no inline script, so it works under the strict CSP (SEC.1).

Bodies are StreamFields with rich-text paragraphs, images and galleries (I18N.3), heroes, calls to
action, FAQs and "featured links" cards to pages, catalog terms and vendors (CMS.2); the product
grid comes with CMS.10.

Translation (I18N.3, spec 09 §9.2): wagtail-localize copies a page into another locale and keeps
it in sync with its source. Texts, alt texts and captions are translated; images are synchronised
from the source, and a translator may override one for a single locale (D-18).

Legal pages (CMS.5) keep an append-only archive of every published text: one history per page,
and so per language, since a translation is its own page (Q-42). A translation keeps its own
effective date through wagtail-localize syncs, and the reviews of each language's text (AI draft,
native-speaker review, legal counsel check) are recorded on its versions (I18N.9). The "Sell
with us" page shows the plans and a FAQ and leads to the VEN.22 application form.
"""

import datetime as dt
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.fields import StreamField
from wagtail.images import get_image_model_string
from wagtail.models import Page, TranslatableMixin
from wagtail_localize.fields import BaseTranslatableField, SynchronizedField
from wagtail_localize.models import Translation

from . import engine
from .blocks import (
    RICH_TEXT_FEATURES,
    CallToActionBlock,
    FaqBlock,
    FeaturedLinksBlock,
    FigureBlock,
    GalleryBlock,
    HeroBlock,
)


def body_field(verbose_name: Any = None) -> StreamField:
    return StreamField(
        [
            ("hero", HeroBlock()),
            ("paragraph", blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label=_("Rich text"))),
            ("figure", FigureBlock()),
            ("gallery", GalleryBlock()),
            ("cta", CallToActionBlock()),
            ("faq", FaqBlock()),
            ("featured", FeaturedLinksBlock()),
        ],
        blank=True,
        verbose_name=verbose_name or _("body"),
    )


class HomePage(Page):  # type: ignore[misc]  # Wagtail is untyped
    """The root page of one language: `/` for EN, `/<code>/` for the others.

    One per locale: a second one would never be served. Checked in `clean()`, which Wagtail runs
    on every save (the page table holds the locale, so no database constraint can).
    """

    body = body_field()

    content_panels: ClassVar[list[Any]] = [*Page.content_panels, FieldPanel("body")]
    parent_page_types: ClassVar[list[str]] = ["wagtailcore.Page"]
    subpage_types: ClassVar[list[str]] = [
        "cms.StandardPage",
        "cms.LegalPage",
        "cms.VendorRecruitmentPage",
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


class StandardPage(Page):  # type: ignore[misc]
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
    body = body_field()

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
    subpage_types: ClassVar[list[str]] = ["cms.StandardPage"]
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

    # Read by the visitor-content fallback (spec 09 §9.2) when it comes: legal pages opt out.
    language_fallback: ClassVar[bool] = False

    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices)
    effective_date = models.DateField(
        _("effective date"),
        help_text=_("Applies from this date; not before the last version's."),
    )
    body = body_field()

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
    body = body_field()
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
