"""Wagtail page types and the per-language navigation (CMS.1, spec 10 §10.2).

Each language has its own page tree under the Wagtail root: one `HomePage` per locale, linked as
translations (same `translation_key`), with the site's pages below it. `/` serves the EN home,
`/it/` the IT one, and so on (I18N.1). Navigation menus are translatable snippets: one header
and one footer menu per locale, falling back to the primary language when a locale has none.
Their links are a StreamField (page or external link): unlike inline panels and FK choosers,
its admin editor needs no inline script, so it works under the strict CSP (SEC.1).

Bodies are StreamFields with rich-text paragraphs, images and galleries (I18N.3), heroes, calls to
action, FAQs and "featured links" cards to pages, catalog terms and vendors (CMS.2); home and
standard pages also take the product grid (CMS.10).

Translation (I18N.3, spec 09 §9.2): wagtail-localize copies a page into another locale and keeps
it in sync with its source. Texts, alt texts and captions are translated; images are synchronised
from the source, and a translator may override one for a single locale (D-18).

Legal pages (CMS.5) keep an append-only archive of every published text: one history per page,
and so per language, since a translation is its own page (Q-42). The "Sell with us" page shows
the plans and a FAQ and leads to the VEN.22 application form.

Landing pages (CMS.4): a term landing page is the editorial part of a term's D-07 listing page
and lives on that URL; a vendor landing page tells an active vendor's story with its products.
"""

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.fields import StreamField
from wagtail.images import get_image_model_string
from wagtail.models import Page, TranslatableMixin
from wagtail_localize.fields import SynchronizedField

from apps.vendors.services import has_public_storefront

from . import engine
from .blocks import (
    RICH_TEXT_FEATURES,
    CallToActionBlock,
    FaqBlock,
    FeaturedLinksBlock,
    FigureBlock,
    GalleryBlock,
    HeroBlock,
    PageCardBlock,
    ProductGridBlock,
)
from .choosers import term_chooser, vendor_chooser


def body_field(verbose_name: Any = None, *, products: bool = False) -> StreamField:
    """A page body; `products` adds the product grid (CMS.10) for the home and standard pages."""
    grid = [("product_grid", ProductGridBlock())] if products else []
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
        ],
        blank=True,
        verbose_name=verbose_name or _("body"),
    )


class HomePage(Page):  # type: ignore[misc]  # Wagtail is untyped
    """The root page of one language: `/` for EN, `/<code>/` for the others.

    One per locale: a second one would never be served. Checked in `clean()`, which Wagtail runs
    on every save (the page table holds the locale, so no database constraint can).
    """

    body = body_field(products=True)

    content_panels: ClassVar[list[Any]] = [*Page.content_panels, FieldPanel("body")]
    parent_page_types: ClassVar[list[str]] = ["wagtailcore.Page"]
    subpage_types: ClassVar[list[str]] = [
        "cms.StandardPage",
        "cms.LegalPage",
        "cms.VendorRecruitmentPage",
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
    body = body_field(products=True)

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


class LegalPage(RoutablePageMixin, Page):  # type: ignore[misc]
    """Terms, privacy, cookies, withdrawal, returns: a text with its effective date.

    Every publish that changes the title, the text or the effective date records a
    `LegalPageVersion` (`services.record_legal_version`, on Wagtail's `page_published`). A
    translation is its own page, so each language has its own versions and dates (Q-42). The
    archive is served at `versions/<number>/`.
    """

    class Kind(models.TextChoices):
        TERMS = "terms", _("Terms of sale")
        PRIVACY = "privacy", _("Privacy policy")
        COOKIES = "cookies", _("Cookie policy")
        WITHDRAWAL = "withdrawal", _("Right of withdrawal")
        RETURNS = "returns", _("Shipping and returns")

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
        return {
            "title": self.title,
            "effective_date": self.effective_date.isoformat(),
            "body": stream_data(self.body),
        }

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
        return {
            "title": self.title,
            "effective_date": self.effective_date.isoformat(),
            "body": stream_data(self.body),
        }


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


class VendorLandingPage(Page):  # type: ignore[misc]
    """A vendor's story beyond its storefront: heritage, workshop, images and its featured
    products (spec 10 §10.2), linked from the storefront. Shown only while the vendor is
    active: otherwise it has no URL and answers 404.
    """

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
