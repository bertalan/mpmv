"""Wagtail page types and the per-language navigation (CMS.1, spec 10 §10.2).

Each language has its own page tree under the Wagtail root: one `HomePage` per locale, linked as
translations (same `translation_key`), with the site's pages below it. `/` serves the EN home,
`/it/` the IT one, and so on (I18N.1). Navigation menus are translatable snippets: one header
and one footer menu per locale, falling back to the primary language when a locale has none.
Their links are a StreamField (page or external link): unlike inline panels and FK choosers,
its admin editor needs no inline script, so it works under the strict CSP (SEC.1).

Bodies are StreamFields with rich-text paragraphs, images and galleries (I18N.3); the other
reusable blocks (hero, product grid, CTA, FAQ) are CMS.2.

Translation (I18N.3, spec 09 §9.2): wagtail-localize copies a page into another locale and keeps
it in sync with its source. Texts, alt texts and captions are translated; images are synchronised
from the source, and a translator may override one for a single locale (D-18).
"""

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.fields import StreamField
from wagtail.images import get_image_model_string
from wagtail.models import Page, TranslatableMixin
from wagtail_localize.fields import SynchronizedField

from .blocks import FigureBlock, GalleryBlock

# Editors may use headings, emphasis, lists and links; no raw HTML, images or embeds yet.
RICH_TEXT_FEATURES = ["h2", "h3", "bold", "italic", "ol", "ul", "link"]


def body_field() -> StreamField:
    return StreamField(
        [
            ("paragraph", blocks.RichTextBlock(features=RICH_TEXT_FEATURES)),
            ("figure", FigureBlock()),
            ("gallery", GalleryBlock()),
        ],
        blank=True,
        verbose_name=_("body"),
    )


class HomePage(Page):  # type: ignore[misc]  # Wagtail is untyped
    """The root page of one language: `/` for EN, `/<code>/` for the others.

    One per locale: a second one would never be served. Checked in `clean()`, which Wagtail runs
    on every save (the page table holds the locale, so no database constraint can).
    """

    body = body_field()

    content_panels: ClassVar[list[Any]] = [*Page.content_panels, FieldPanel("body")]
    parent_page_types: ClassVar[list[str]] = ["wagtailcore.Page"]
    subpage_types: ClassVar[list[str]] = ["cms.StandardPage"]
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
