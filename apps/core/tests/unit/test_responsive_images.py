"""THM.12: responsive renditions (spec 14 §14.7) and the image settings.

Raster renditions default to WebP; templates ask for `<picture>` renditions in AVIF, WebP and a
JPEG fallback at a few widths, with `sizes`. When the image library cannot encode AVIF, AVIF is
left out (WebP and JPEG remain) and a system check says so.
"""

import pytest
from django.conf import settings

from apps.core import images
from apps.core.checks import check_avif_support


def test_uploads_accept_the_raster_formats_and_svg() -> None:
    assert set(settings.WAGTAILIMAGES_EXTENSIONS) == {
        "avif",
        "gif",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "svg",
    }


def test_raster_renditions_default_to_webp_and_animated_gifs_keep_their_format() -> None:
    conversions = settings.WAGTAILIMAGES_FORMAT_CONVERSIONS
    for source in ("png", "jpeg", "bmp", "heic", "webp"):
        assert conversions[source] == "webp"
    assert conversions["avif"] == "avif"
    # Wagtail converts still GIFs to PNG and keeps animated ones; WebP would drop the animation.
    assert "gif" not in conversions


def test_picture_formats_put_avif_first_and_jpeg_last() -> None:
    assert images.picture_formats(avif=True) == ("avif", "webp", "jpeg")


def test_without_avif_support_the_picture_falls_back_to_webp() -> None:
    assert images.picture_formats(avif=False) == ("webp", "jpeg")


def test_a_preset_gives_one_spec_per_format_and_width() -> None:
    specs = images.picture_specs("card", formats=("avif", "webp", "jpeg"))

    widths = images.PRESETS["card"].widths
    assert len(specs) == 3 * len(widths)
    assert f"format-avif|width-{widths[0]}" in specs
    assert f"format-jpeg|width-{widths[-1]}" in specs


def test_a_fill_preset_scales_its_crop() -> None:
    specs = images.picture_specs("gallery", formats=("webp",))

    assert specs == ["format-webp|fill-480x360", "format-webp|fill-960x720"]


def test_an_svg_keeps_its_format_and_only_resizes() -> None:
    specs = images.picture_specs("logo", formats=("avif", "webp", "jpeg"), svg=True)

    assert specs
    assert all("format-" not in spec for spec in specs)


@pytest.mark.parametrize("name", sorted(images.PRESETS))
def test_every_preset_has_sizes_and_increasing_widths(name: str) -> None:
    preset = images.PRESETS[name]
    assert preset.sizes
    assert list(preset.widths) == sorted(set(preset.widths))


def test_an_unknown_preset_is_an_error() -> None:
    with pytest.raises(KeyError):
        images.picture_specs("poster", formats=("webp",))


def test_the_check_warns_when_avif_cannot_be_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(images, "avif_supported", lambda: False)
    messages = check_avif_support(None)
    assert [m.id for m in messages] == ["core.W501"]

    monkeypatch.setattr(images, "avif_supported", lambda: True)
    assert check_avif_support(None) == []


def test_the_image_library_in_this_environment_encodes_avif_and_webp() -> None:
    # The wheels in uv.lock (and so the container image) bundle libavif and libwebp.
    assert images.avif_supported()
    assert images.webp_supported()
