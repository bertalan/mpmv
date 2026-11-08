"""The blocks of CMS page bodies (I18N.3, CMS.2, spec 10 §10.2), translated with wagtail-localize.

wagtail-localize turns a block's text fields into translatable segments and its image chooser
into an overridable segment: alt texts and captions are translated (the EN text stays until a
translation is published) and a translator can replace an image in one locale only. A gallery
also applies the D-18 replacement rule (`engine.gallery_for_language`).

CMS.2 adds the hero, the call to action, the FAQ and the "featured links" cards. Their templates
are minimal, semantic and CSP-safe (no inline script or style); the `base` theme restyles them
(THM.3, THM.4). A link points to a live page of the site, followed into the reader's language,
or to an external URL. The FAQs of a page give one FAQPage (`cms_tags.faq_jsonld`).

Cards (part 2/2) point to a page, a catalog taxonomy term or a vendor storefront, chosen in the
admin (`choosers`), never typed. A card whose target is not public is left out: a page that is
not live, a term (or its taxonomy or parent terms) without published texts, a vendor that is not
active. Term and storefront URLs follow spec 03 (`engine.term_path`, `engine.storefront_path`).
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.blocks import StructBlockValidationError
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page

from apps.catalog.models import Term
from apps.core import translations
from apps.vendors.models import Vendor
from apps.vendors.services import has_public_storefront

from .choosers import term_chooser, vendor_chooser
from .engine import NEUTRAL, gallery_for_language, link_language, storefront_path, term_path

# Editors may use headings, emphasis, lists and links; no raw HTML, images or embeds yet.
RICH_TEXT_FEATURES = ["h2", "h3", "bold", "italic", "ol", "ul", "link"]


def language_choices() -> list[tuple[str, Any]]:
    """Neutral first, then every configured language; read at runtime, so no migration."""
    return [(NEUTRAL, _("All languages (neutral)")), *settings.LANGUAGES]


class FigureBlock(blocks.StructBlock):  # type: ignore[misc]  # Wagtail is untyped
    """One image with its alt text and an optional caption."""

    image = ImageChooserBlock()
    alt_text = blocks.CharBlock(
        max_length=250, help_text=_("Describes the image for people who cannot see it.")
    )
    caption = blocks.CharBlock(max_length=250, required=False)

    class Meta:
        icon = "image"
        label = _("Image")
        template = "cms/blocks/figure.html"


class GalleryImageBlock(FigureBlock):
    """A gallery image, neutral or dedicated to one language (D-18)."""

    language = blocks.ChoiceBlock(
        choices=language_choices,
        required=False,
        help_text=_("Shown instead of the neutral images in its language."),
    )


class GalleryBlock(blocks.StructBlock):  # type: ignore[misc]
    """Images shown by the D-18 rule: a language's dedicated images, otherwise the neutral."""

    images = blocks.ListBlock(GalleryImageBlock())

    class Meta:
        icon = "image"
        label = _("Gallery")
        template = "cms/blocks/gallery.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        context["images"] = gallery_for_language(value["images"], _page_language(parent_context))
        return context


def live_page(page: Page | None) -> Page | None:
    """`page` in the active language when that translation is live, else `page` itself when it
    is live, else None: a link to a draft, unpublished or expired page is dropped."""
    if page is None:
        return None
    target: Page = page.specific.localized
    return target if target.live else None


def _page_language(parent_context: dict[str, Any] | None) -> str:
    page = (parent_context or {}).get("page")
    return str(page.locale.language_code if page is not None else get_language())


class LinkValue(blocks.StructValue):  # type: ignore[misc]
    def href(self) -> str:
        """The live page in the active language (or its source), or the URL; "" for no link."""
        if self.get("page") is not None:
            page = live_page(self.get("page"))
            url: str | None = page.url if page is not None else None
            return url or ""
        return str(self.get("url") or "")


class LinkBlock(blocks.StructBlock):  # type: ignore[misc]
    """A labelled link to a page of the site or to an external URL, never both."""

    page = blocks.PageChooserBlock(required=False)
    url = blocks.URLBlock(required=False, label=_("URL"))
    label = blocks.CharBlock(max_length=100)

    class Meta:
        icon = "link"
        label = _("Link")
        value_class = LinkValue

    def clean(self, value: LinkValue) -> LinkValue:
        value = super().clean(value)
        if (value.get("page") is None) == (not value.get("url")):
            raise StructBlockValidationError(
                non_block_errors=[ValidationError(_("Choose a page or enter a URL, not both."))]
            )
        return value


class HeroBlock(blocks.StructBlock):  # type: ignore[misc]
    """The opening banner of a page: a heading, a short text, an image and up to two links."""

    heading = blocks.CharBlock(max_length=120)
    text = blocks.TextBlock(required=False, max_length=400)
    image = ImageChooserBlock(required=False)
    alt_text = blocks.CharBlock(
        required=False,
        max_length=250,
        help_text=_("Describes the image for people who cannot see it."),
    )
    links = blocks.ListBlock(LinkBlock(), min_num=0, max_num=2, default=[])

    class Meta:
        icon = "pick"
        label = _("Hero")
        template = "cms/blocks/hero.html"

    def clean(self, value: blocks.StructValue) -> blocks.StructValue:
        value = super().clean(value)
        if value.get("image") is not None and not (value.get("alt_text") or "").strip():
            raise StructBlockValidationError(
                block_errors={"alt_text": ValidationError(_("Describe the image."))}
            )
        return value


class CallToActionBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading, a short text and one link that invites the reader to act."""

    heading = blocks.CharBlock(max_length=120)
    text = blocks.TextBlock(required=False, max_length=400)
    link = LinkBlock()

    class Meta:
        icon = "pick"
        label = _("Call to action")
        template = "cms/blocks/cta.html"


class FaqItemBlock(blocks.StructBlock):  # type: ignore[misc]
    question = blocks.CharBlock(max_length=250)
    answer = blocks.RichTextBlock(features=RICH_TEXT_FEATURES)

    class Meta:
        icon = "help"
        label = _("Question")


class FaqBlock(blocks.StructBlock):  # type: ignore[misc]
    """Questions shown as disclosures (`<details>`). The page template emits one FAQPage for all
    the FAQ blocks of the page (`cms_tags.faq_jsonld`, spec 10)."""

    heading = blocks.CharBlock(required=False, max_length=120)
    items = blocks.ListBlock(FaqItemBlock(), min_num=1, label=_("Questions"))

    class Meta:
        icon = "help"
        label = _("FAQ")
        template = "cms/blocks/faq.html"


# --- Cards (part 2/2)


@dataclass(frozen=True)
class Card:
    label: str
    href: str
    text: str


class _CardValue(blocks.StructValue):  # type: ignore[misc]
    def card(self, language: str) -> Card | None:
        """The card in `language`, or None when its target is not public."""
        raise NotImplementedError

    def _card(self, label: str, href: str, text: str = "") -> Card:
        return Card(self.get("label") or label, href, self.get("text") or text)


class PageCardValue(_CardValue):
    def card(self, language: str) -> Card | None:
        page = live_page(self.get("page"))
        if page is None or not page.url:
            return None
        return self._card(page.title, page.url)


def _published_languages(obj: Any) -> dict[str, Any]:
    return {
        row.language: row for row in obj.translations.all() if row.status == translations.PUBLISHED
    }


class TermCardValue(_CardValue):
    def card(self, language: str) -> Card | None:
        """Its name and listing URL in `language`, or in the primary language when the term, its
        taxonomy or a parent term is not published in `language` (spec 09 fallback)."""
        term: Term | None = self.get("term")
        if term is None:
            return None
        terms = [*term.get_ancestors(), term]
        texts = [_published_languages(item) for item in (term.taxonomy, *terms)]
        chosen = link_language(language, translations.source_language(), texts)
        if chosen is None:
            return None
        taxonomy, *term_texts = (by_language[chosen] for by_language in texts)
        href = term_path(
            taxonomy.slug,
            [text.slug for text in term_texts],
            chosen,
            translations.source_language(),
        )
        return self._card(term_texts[-1].name, href)


class VendorCardValue(_CardValue):
    def card(self, language: str) -> Card | None:
        vendor: Vendor | None = self.get("vendor")
        if vendor is None or not has_public_storefront(vendor):
            return None
        href = storefront_path(vendor.slug, language, translations.source_language())
        texts = vendor.translation(language).translation
        return self._card(vendor.display_name, href, texts.tagline if texts is not None else "")


class TermChooserBlock(term_chooser.get_block_class("_TermChooserBlock", __name__)):  # type: ignore[misc]
    """A catalog taxonomy term, chosen in the term chooser modal."""


class VendorChooserBlock(vendor_chooser.get_block_class("_VendorChooserBlock", __name__)):  # type: ignore[misc]
    """A vendor with a public storefront, chosen in the vendor chooser modal."""

    def clean(self, value: Vendor | None) -> Vendor | None:
        value = super().clean(value)
        if value is not None and not has_public_storefront(value):
            raise ValidationError(_("Choose an active vendor."))
        return value


def _card_label() -> blocks.CharBlock:
    return blocks.CharBlock(
        required=False, max_length=100, help_text=_("Leave empty to use its name.")
    )


def _card_text() -> blocks.TextBlock:
    return blocks.TextBlock(required=False, max_length=250)


class PageCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a page of the site, in the reader's language."""

    page = blocks.PageChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "doc-empty"
        label = _("Page")
        value_class = PageCardValue


class TermCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to a catalog term's listing (spec 03), with its name in the reader's language."""

    term = TermChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "tag"
        label = _("Term")
        value_class = TermCardValue


class VendorCardBlock(blocks.StructBlock):  # type: ignore[misc]
    """A card to an active vendor's storefront, with its tagline."""

    vendor = VendorChooserBlock()
    label = _card_label()
    text = _card_text()

    class Meta:
        icon = "group"
        label = _("Vendor")
        value_class = VendorCardValue


class FeaturedLinksBlock(blocks.StructBlock):  # type: ignore[misc]
    """A heading and cards to pages, terms and vendors; left out when no card is public."""

    heading = blocks.CharBlock(required=False, max_length=120)
    cards = blocks.StreamBlock(
        [("page", PageCardBlock()), ("term", TermCardBlock()), ("vendor", VendorCardBlock())],
        min_num=1,
        label=_("Cards"),
    )

    class Meta:
        icon = "list-ul"
        label = _("Featured links")
        template = "cms/blocks/featured.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        language = _page_language(parent_context)
        cards = (child.value.card(language) for child in value["cards"])
        context["cards"] = [card for card in cards if card is not None]
        return context
