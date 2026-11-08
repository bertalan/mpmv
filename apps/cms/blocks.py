"""Image blocks of CMS page bodies, translated with wagtail-localize (I18N.3, spec 10).

wagtail-localize turns a block's text fields into translatable segments and its image chooser
into an overridable segment: alt texts and captions are translated (the EN text stays until a
translation is published) and a translator can replace an image in one locale only. A gallery
also applies the D-18 replacement rule (`engine.gallery_for_language`). CMS.2 builds the rest
of the block library on these.
"""

from typing import Any

from django.conf import settings
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock

from .engine import NEUTRAL, gallery_for_language


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
