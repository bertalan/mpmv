"""I18N.3: what wagtail-localize translates, synchronises and lets a translator override.

Spec 10 "Images per locale": image fields on page models are synchronised and overridable,
image chooser blocks stay overridable segments, alt text and captions are translatable.
"""

from django.conf import settings
from wagtail import blocks
from wagtail.images import get_image_model
from wagtail.images.blocks import ImageChooserBlock
from wagtail_localize.fields import SynchronizedField, TranslatableField, get_translatable_fields

from apps.cms.blocks import FigureBlock, GalleryBlock, GalleryImageBlock, language_choices
from apps.cms.engine import NEUTRAL
from apps.cms.models import LegalPage, StandardPage
from apps.core.models import MediaImage


def _fields(model: type) -> dict[str, SynchronizedField | TranslatableField]:
    return {field.field_name: field for field in get_translatable_fields(model)}


def test_wagtail_localize_and_the_shared_image_model_are_installed() -> None:
    assert "wagtail_localize" in settings.INSTALLED_APPS
    assert "wagtail.images" in settings.INSTALLED_APPS
    # Spec 10 §10.2: the custom model is the image model before any image exists (CMS.8).
    assert settings.WAGTAILIMAGES_IMAGE_MODEL == "core.MediaImage"
    assert get_image_model() is MediaImage


def test_the_page_image_is_synchronised_and_overridable_per_locale() -> None:
    image = _fields(StandardPage)["image"]
    assert isinstance(image, SynchronizedField)
    assert image.overridable


def test_page_texts_alt_text_and_caption_are_translatable() -> None:
    fields = _fields(StandardPage)
    for name in ("title", "slug", "body", "image_alt_text", "image_caption"):
        assert isinstance(fields[name], TranslatableField), name


def test_the_figure_block_has_an_image_chooser_and_translatable_texts() -> None:
    children = FigureBlock().child_blocks
    assert isinstance(children["image"], ImageChooserBlock)
    assert isinstance(children["alt_text"], blocks.CharBlock)
    assert children["alt_text"].required
    assert isinstance(children["caption"], blocks.CharBlock)
    assert not children["caption"].required


def test_each_gallery_image_is_neutral_or_dedicated_to_one_configured_language() -> None:
    gallery = GalleryBlock().child_blocks["images"]
    assert isinstance(gallery, blocks.ListBlock)
    item = gallery.child_block
    assert isinstance(item, GalleryImageBlock)
    assert isinstance(item.child_blocks["language"], blocks.ChoiceBlock)
    assert not item.child_blocks["language"].required

    codes = [code for code, _label in language_choices()]
    assert codes[0] == NEUTRAL
    assert codes[1:] == [code for code, _name in settings.LANGUAGES]


def test_page_bodies_offer_the_figure_and_gallery_blocks() -> None:
    body = StandardPage._meta.get_field("body").stream_block.child_blocks
    assert isinstance(body["figure"], FigureBlock)
    assert isinstance(body["gallery"], GalleryBlock)


def test_a_legal_translation_keeps_its_own_effective_date() -> None:
    """I18N.9 (Q-42): each language has its own version and effective date, so a sync from the
    source page neither copies the date nor offers it as a segment to translate."""
    date = _fields(LegalPage)["effective_date"]
    page = LegalPage.__new__(LegalPage)  # the checks never read the instance; no database
    assert not date.is_synchronized(page)
    assert not date.is_translated(page)
    assert not date.is_overridable(page)


def test_the_legal_page_kind_follows_the_source_page() -> None:
    assert isinstance(_fields(LegalPage)["kind"], SynchronizedField)
