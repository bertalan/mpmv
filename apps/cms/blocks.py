"""The blocks of CMS page bodies (I18N.3, CMS.2, spec 10 §10.2), translated with wagtail-localize.

wagtail-localize turns a block's text fields into translatable segments and its image chooser
into an overridable segment: alt texts and captions are translated (the EN text stays until a
translation is published) and a translator can replace an image in one locale only. A gallery
also applies the D-18 replacement rule (`engine.gallery_for_language`).

CMS.2 adds the hero, the call to action and the FAQ. Their templates are minimal, semantic and
CSP-safe (no inline script or style); the `base` theme restyles them (THM.3, THM.4). A link
points to a page of the site, followed into the reader's language, or to an external URL.
"""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.blocks import StructBlockValidationError
from wagtail.images.blocks import ImageChooserBlock

from .engine import NEUTRAL, faq_page_jsonld, gallery_for_language, jsonld_text

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
        page = (parent_context or {}).get("page")
        language = page.locale.language_code if page is not None else get_language()
        context["images"] = gallery_for_language(value["images"], language)
        return context


class LinkValue(blocks.StructValue):  # type: ignore[misc]
    def href(self) -> str:
        """The page in the active language when it has a translation, otherwise the URL."""
        page = self.get("page")
        if page is not None:
            url: str | None = page.specific.localized.url
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
    """Questions shown as disclosures (`<details>`), with FAQPage structured data (spec 10)."""

    heading = blocks.CharBlock(required=False, max_length=120)
    items = blocks.ListBlock(FaqItemBlock(), min_num=1, label=_("Questions"))

    class Meta:
        icon = "help"
        label = _("FAQ")
        template = "cms/blocks/faq.html"

    def get_context(self, value: Any, parent_context: dict[str, Any] | None = None) -> Any:
        context = super().get_context(value, parent_context=parent_context)
        questions = [(item["question"], str(item["answer"])) for item in value["items"]]
        # JSON with <, > and & escaped: it cannot leave its script element (`jsonld_text`).
        context["jsonld"] = mark_safe(jsonld_text(faq_page_jsonld(questions)))  # noqa: S308
        return context
