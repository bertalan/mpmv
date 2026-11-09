"""SEC.2: the upload checks and the image re-encoding, run on the malicious-upload corpus.

`tests/upload_corpus.py` builds the files; `tests/integration/test_upload_paths.py` runs the same
cases through every upload path of the site.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from apps.core.uploads import (
    ImageLimits,
    UploadRefused,
    check_document,
    extension_of,
    safe_filename,
    sanitise_image,
    upload_kind,
)
from tests.upload_corpus import (
    SVG_WITH_SCRIPT,
    Case,
    assert_safe_document,
    assert_safe_image,
    document_cases,
    image_cases,
    name_cases,
    png,
)

# The defaults decided for SEC.2 (lane-a on #164): 25 MB, 100 megapixels decoded (all frames),
# 16000 px a side, originals stored within 40 megapixels.
LIMITS = ImageLimits(
    max_bytes=25 * 1024 * 1024,
    max_pixels=100_000_000,
    max_side=16_000,
    max_stored_pixels=40_000_000,
)
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def _params(cases: list[Case]) -> list[object]:
    return [pytest.param(case, id=case.id) for case in cases]


@pytest.mark.parametrize("case", _params(image_cases() + name_cases()))
def test_the_image_checks_refuse_or_re_encode_every_corpus_file(case: Case) -> None:
    if case.verdict == "refuse":
        with pytest.raises(UploadRefused):
            sanitise_image(case.data, case.filename, LIMITS)
        return
    clean = sanitise_image(case.data, case.filename, LIMITS)
    assert_safe_image(f"original_images/{clean.name}", clean.data, case)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert (clean.width, clean.height) == stored.size


@pytest.mark.parametrize("case", _params(document_cases()))
def test_the_document_checks_refuse_or_accept_every_corpus_file(case: Case) -> None:
    if case.verdict == "refuse":
        with pytest.raises(UploadRefused):
            check_document(case.data, case.filename, max_bytes=MAX_DOCUMENT_BYTES)
        return
    name = check_document(case.data, case.filename, max_bytes=MAX_DOCUMENT_BYTES)
    assert_safe_document(f"documents/{name}", case.data, case)


def test_a_bomb_is_refused_before_its_pixels_are_decoded() -> None:
    """The caps apply to the header: 10 gigapixels are never allocated."""
    case = next(c for c in image_cases() if c.id == "huge-dimensions-tiny-file")
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(case.data, case.filename, LIMITS)
    assert refusal.value.code in {"too_wide", "too_many_pixels"}


def test_the_caps_are_the_limits_given() -> None:
    data = Image.new("RGB", (300, 200))
    buffer = io.BytesIO()
    data.save(buffer, format="PNG")
    small = ImageLimits(max_bytes=10_000, max_pixels=50_000, max_side=1000)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(buffer.getvalue(), "wide.png", small)
    assert refusal.value.code == "too_many_pixels"
    narrow = ImageLimits(max_bytes=10_000, max_pixels=1_000_000, max_side=250)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(buffer.getvalue(), "wide.png", narrow)
    assert refusal.value.code == "too_wide"
    tiny = ImageLimits(max_bytes=10, max_pixels=1_000_000, max_side=1000)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(buffer.getvalue(), "wide.png", tiny)
    assert refusal.value.code == "too_large"


def _png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


SMALL = ImageLimits(max_bytes=100_000, max_pixels=10_000, max_side=1000, max_stored_pixels=2_500)


def test_an_original_over_the_stored_cap_is_downscaled_keeping_its_aspect_ratio() -> None:
    """Accepted up to the decode cap, stored within the stored cap (lane-a decision on #164)."""
    clean = sanitise_image(_png((100, 60)), "wide.png", SMALL)
    assert (clean.width, clean.height) == (64, 38)  # 6000 px scaled by sqrt(2500 / 6000)
    assert clean.width * clean.height <= SMALL.max_stored_pixels
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert stored.size == (64, 38)


def test_an_original_within_the_stored_cap_keeps_its_size() -> None:
    clean = sanitise_image(_png((50, 50)), "square.png", SMALL)
    assert (clean.width, clean.height) == (50, 50)


def test_an_animation_over_the_stored_cap_is_downscaled_frame_by_frame() -> None:
    frames = [Image.new("RGB", (40, 30), (80 * n, 0, 0)) for n in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=80)
    limits = ImageLimits(
        max_bytes=100_000, max_pixels=10_000, max_side=1000, max_stored_pixels=1_200
    )
    clean = sanitise_image(buffer.getvalue(), "anim.gif", limits)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert getattr(stored, "n_frames", 1) == 3
        assert stored.size == (clean.width, clean.height) == (23, 17)
    assert clean.width * clean.height * 3 <= limits.max_stored_pixels


def test_pillows_bomb_guard_follows_the_decode_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pillow's own threshold, set to the cap at startup (`CoreConfig.ready`), does not refuse
    what the cap accepts; above the cap the image is refused."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", SMALL.max_pixels)
    clean = sanitise_image(_png((100, 60)), "wide.png", SMALL)
    assert (clean.width, clean.height) == (64, 38)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(_png((200, 60)), "wide.png", SMALL)
    assert refusal.value.code == "too_many_pixels"


def test_an_upload_never_changes_pillows_process_wide_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The threshold is set once at startup, so it does not depend on whether an upload ran."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 7_000)
    sanitise_image(_png((50, 50)), "square.png", SMALL)
    assert Image.MAX_IMAGE_PIXELS == 7_000


def test_pillows_threshold_is_the_decode_cap_from_startup() -> None:
    from django.conf import settings

    assert Image.MAX_IMAGE_PIXELS == settings.WAGTAILIMAGES_MAX_IMAGE_PIXELS


def test_an_animation_keeps_its_frames_and_a_png_its_transparency() -> None:
    frames = [Image.new("RGB", (10, 10), (80 * n, 0, 0)) for n in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=80)
    clean = sanitise_image(buffer.getvalue(), "anim.gif", LIMITS)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert getattr(stored, "n_frames", 1) == 3

    buffer = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buffer, format="PNG")
    clean = sanitise_image(buffer.getvalue(), "clear.png", LIMITS)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert stored.mode == "RGBA"
        assert stored.getpixel((0, 0)) == (0, 0, 0, 0)


def test_re_encoding_is_deterministic() -> None:
    """Wagtail finds duplicate uploads by the stored file's hash."""
    case = next(c for c in image_cases() if c.id == "jpeg-exif-gps")
    first = sanitise_image(case.data, case.filename, LIMITS)
    again = sanitise_image(case.data, case.filename, LIMITS)
    assert first.data == again.data


@pytest.mark.parametrize(
    ("name", "extension"),
    [
        ("photo.PNG", "png"),
        ("photo.png.exe", "exe"),
        ("photo.png.", "png"),
        (".htaccess", ""),
        ("photo", ""),
        ("../x/y.jpg", "jpg"),
        ("a\\b.gif", "gif"),
    ],
)
def test_the_extension_is_the_one_a_browser_would_see(name: str, extension: str) -> None:
    assert extension_of(name) == extension


def test_a_safe_file_name_never_keeps_a_second_extension() -> None:
    assert safe_filename("shell.php.png", "png") == "shell-php.png"
    assert safe_filename("x.tar.gz.jpeg", "jpg") == "x-tar-gz.jpg"
    assert safe_filename("lpt9.webp", "webp") == "image-lpt9.webp"


def _gif_frames(count: int) -> bytes:
    frames = [Image.new("L", (2, 2), (n % 2) * 255) for n in range(count)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=50)
    return buffer.getvalue()


def test_too_many_frames_are_refused_before_any_frame_is_decoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame-count bomb: few pixels, but every frame costs CPU to decode and re-encode."""
    import apps.core.uploads as uploads

    def no_decoding(image: Image.Image) -> None:
        raise AssertionError("frames were decoded before the frame cap was checked")

    monkeypatch.setattr(uploads, "_load", no_decoding)
    limits = ImageLimits(max_bytes=10_000_000, max_pixels=1_000_000, max_side=1000, max_frames=10)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(_gif_frames(11), "anim.gif", limits)
    assert refusal.value.code == "too_many_frames"
    assert "10" in refusal.value.message


def test_an_animation_at_the_frame_cap_is_stored() -> None:
    limits = ImageLimits(max_bytes=10_000_000, max_pixels=1_000_000, max_side=1000, max_frames=10)
    clean = sanitise_image(_gif_frames(10), "anim.gif", limits)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert getattr(stored, "n_frames", 1) == 10


def test_the_default_frame_cap_is_set() -> None:
    assert 0 < LIMITS.max_frames <= 1000


@pytest.mark.parametrize("check_name", [True, False])
def test_an_mpo_photo_is_stored_as_a_single_frame_jpeg(check_name: bool) -> None:
    """Multi-picture JPEGs (phones, cameras) are JPEGs: the first picture is kept."""
    case = next(c for c in image_cases() if c.id == "mpo-photo")
    clean = sanitise_image(case.data, "phone.jpg", LIMITS, check_name=check_name)
    assert clean.format == "JPEG"
    assert clean.name == "phone.jpg"
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert stored.format == "JPEG"
        assert getattr(stored, "n_frames", 1) == 1
        red, _green, blue = stored.convert("RGB").getpixel((5, 5))  # type: ignore[misc]
        assert red > 150  # the first picture, not the second
        assert blue < 100


def test_an_unknown_detected_format_is_refused_never_a_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever Pillow reports, an unexpected format is a refusal (a 400), not a crash (a 500)."""
    import apps.core.uploads as uploads

    def open_anything(data: bytes, limits: ImageLimits) -> Image.Image:
        return Image.open(io.BytesIO(data))

    monkeypatch.setattr(uploads, "_open", open_anything)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="BMP")
    for check_name in (True, False):
        with pytest.raises(UploadRefused):
            sanitise_image(buffer.getvalue(), "photo.png", LIMITS, check_name=check_name)


def _srgb_bytes() -> bytes:
    from PIL import ImageCms

    return bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_an_icc_profile_is_converted_to_srgb_and_not_kept(fmt: str) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 40, 40)).save(buffer, format=fmt, icc_profile=_srgb_bytes())
    clean = sanitise_image(buffer.getvalue(), f"x.{fmt.lower().replace('jpeg', 'jpg')}", LIMITS)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert "icc_profile" not in stored.info
        red, green, blue = stored.convert("RGB").getpixel((8, 8))  # type: ignore[misc]
    # sRGB to sRGB: the colours are unchanged (up to lossy re-encoding).
    assert abs(red - 200) <= 6
    assert abs(green - 40) <= 6
    assert abs(blue - 40) <= 6


def test_a_non_srgb_profile_is_applied_to_the_pixels() -> None:
    """A wide-gamut source is converted, so it does not turn dull once its profile is gone."""
    from PIL import ImageCms

    # A profile that differs from sRGB: a gamma-1.0 RGB with sRGB primaries.
    linear: ImageCms.ImageCmsProfile = _linear_rgb_profile()  # type: ignore[assignment]
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (128, 128, 128)).save(
        buffer, format="PNG", icc_profile=linear.tobytes()
    )
    clean = sanitise_image(buffer.getvalue(), "linear.png", LIMITS)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert "icc_profile" not in stored.info
        grey = stored.convert("RGB").getpixel((8, 8))
    # Linear 0.5 is about 188 in sRGB.
    assert grey[0] > 170  # type: ignore[index]


def _linear_rgb_profile() -> object:
    """A minimal ICC v2 display profile: sRGB primaries, linear (gamma 1.0) tone curves."""
    import struct

    from PIL import ImageCms

    def s15(value: float) -> bytes:
        return struct.pack(">i", round(value * 65536))

    def xyz(x: float, y: float, z: float) -> bytes:
        return b"XYZ \0\0\0\0" + s15(x) + s15(y) + s15(z)

    identity = b"curv\0\0\0\0" + struct.pack(">I", 0)
    description = b"desc\0\0\0\0" + struct.pack(">I", 7) + b"linear\0" + b"\0" * 79
    tags = [
        (b"desc", description),
        (b"wtpt", xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", xyz(0.4361, 0.2225, 0.0139)),
        (b"gXYZ", xyz(0.3851, 0.7169, 0.0971)),
        (b"bXYZ", xyz(0.1431, 0.0606, 0.7141)),
        (b"rTRC", identity),
        (b"gTRC", identity),
        (b"bTRC", identity),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, body = b"", b""
    for signature, data in tags:
        data += b"\0" * (-len(data) % 4)
        table += signature + struct.pack(">II", offset + len(body), len(data))
        body += data
    size = offset + len(body)
    header = (
        struct.pack(">I", size)
        + b"\0\0\0\0"
        + struct.pack(">I", 0x02100000)
        + b"mntrRGB XYZ "
        + b"\0" * 12
        + b"acsp"
        + b"\0" * 24
        + struct.pack(">I", 0)
        + xyz(0.9642, 1.0, 0.8249)[8:]
        + b"\0" * 48
    )
    assert len(header) == 128
    return ImageCms.ImageCmsProfile(
        io.BytesIO(header + struct.pack(">I", len(tags)) + table + body)
    )


def test_an_unreadable_or_oversized_profile_is_dropped() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 40, 40)).save(
        buffer, format="PNG", icc_profile=b"not a profile" + b"\x00" * 200_000
    )
    clean = sanitise_image(buffer.getvalue(), "x.png", LIMITS)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert "icc_profile" not in stored.info
        assert stored.convert("RGB").getpixel((8, 8)) == (200, 40, 40)


def _animation(fmt: str, count: int) -> bytes:
    frames = [Image.new("RGB", (4, 4), (n % 2 * 255, 0, 0)) for n in range(count)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format=fmt, save_all=True, append_images=frames[1:], duration=50)
    return buffer.getvalue()


@pytest.mark.parametrize(("fmt", "name"), [("GIF", "a.gif"), ("PNG", "a.png"), ("WEBP", "a.webp")])
def test_the_frame_cap_holds_for_every_animated_format(fmt: str, name: str) -> None:
    limits = ImageLimits(max_bytes=10_000_000, max_pixels=1_000_000, max_side=1000, max_frames=10)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(_animation(fmt, 11), name, limits)
    assert refusal.value.code == "too_many_frames"
    clean = sanitise_image(_animation(fmt, 10), name, limits)
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert getattr(stored, "n_frames", 1) == 10


def test_gif_frames_are_counted_only_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GIF's frame count walks every frame header: the walk stops one frame past the cap."""
    from PIL import GifImagePlugin

    seen: list[int] = []
    original = GifImagePlugin.GifImageFile._seek

    def counting(self: GifImagePlugin.GifImageFile, frame: int, update_image: bool = True) -> None:
        seen.append(frame)
        original(self, frame, update_image)

    monkeypatch.setattr(GifImagePlugin.GifImageFile, "_seek", counting)
    limits = ImageLimits(max_bytes=10_000_000, max_pixels=1_000_000, max_side=1000, max_frames=10)
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(_gif_frames(200), "anim.gif", limits)
    assert refusal.value.code == "too_many_frames"
    assert max(seen) <= limits.max_frames + 1


def test_an_animation_builds_one_colour_transform_and_decodes_each_frame_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import ImageCms

    built: list[object] = []
    original = ImageCms.buildTransform

    def counting(*args: object, **kwargs: object) -> object:
        built.append(args)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ImageCms, "buildTransform", counting)
    frames = [Image.new("RGB", (8, 8), (n * 40, 0, 0)) for n in range(6)]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="PNG",
        save_all=True,
        append_images=frames[1:],
        duration=[10, 20, 30, 40, 50, 60],
        icc_profile=_srgb_bytes(),
    )
    clean = sanitise_image(buffer.getvalue(), "anim.png", LIMITS)
    assert len(built) == 1
    with Image.open(io.BytesIO(clean.data)) as stored:
        assert getattr(stored, "n_frames", 1) == 6
        durations = []
        for index in range(6):
            stored.seek(index)
            durations.append(stored.info.get("duration"))
    assert durations == [10, 20, 30, 40, 50, 60]


def _mpo(primary: tuple[int, int], second: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", primary, (200, 40, 40)).save(
        buffer,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", second, (40, 40, 200))],
    )
    return buffer.getvalue()


def test_an_mpo_counts_and_decodes_only_its_first_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two pictures over the decode cap together, the first one alone within it: stored, scaled
    down to the stored cap, and the second picture is never decoded."""
    from PIL import MpoImagePlugin

    sought: list[int] = []
    original = MpoImagePlugin.MpoImageFile.seek

    def recording(self: MpoImagePlugin.MpoImageFile, frame: int) -> None:
        sought.append(frame)
        original(self, frame)

    monkeypatch.setattr(MpoImagePlugin.MpoImageFile, "seek", recording)
    data = _mpo((100, 60), (100, 60))  # 6000 px each, 12000 together; SMALL decodes 10000
    clean = sanitise_image(data, "phone.jpg", SMALL)
    assert clean.format == "JPEG"
    assert (clean.width, clean.height) == (64, 38)
    assert all(frame == 0 for frame in sought)


PEAK_SCRIPT = """
import sys
from apps.core.uploads import ImageLimits, sanitise_image


def high_water_mark():
    # VmHWM: this process's resident peak, reset by exec (ru_maxrss keeps the parent's).
    with open("/proc/self/status") as status:
        line = next(line for line in status if line.startswith("VmHWM:"))
    return int(line.split()[1]) * 1024


data = open(sys.argv[1], "rb").read()
limits = ImageLimits(
    max_bytes=25 * 1024 * 1024, max_pixels=30_000_000, max_side=16_000,
    max_stored_pixels=10_000_000,
)
before = high_water_mark()
clean = sanitise_image(data, sys.argv[2], limits)
print(high_water_mark() - before, clean.width, clean.height)
"""


@pytest.mark.skipif(
    not Path("/proc/self/status").exists(), reason="reads the peak from Linux's /proc"
)
@pytest.mark.parametrize(
    ("mode", "fmt", "name"),
    [("RGBA", "PNG", "big.png"), ("RGB", "JPEG", "big.jpg")],
)
def test_one_upload_peaks_at_its_decoded_size_plus_the_stored_size(
    tmp_path: Path, mode: str, fmt: str, name: str
) -> None:
    """Peak memory of one re-encoding, measured in its own process: the decoded original
    (4 bytes a pixel in Pillow) and the stored copy, no full-size intermediate copies.

    25 megapixels decode to 100 MB and are stored at 10 (40 MB). The budget, 1.8 times the
    decoded size, holds the original, the stored copy, one resampling band and the encoder's
    buffers (measured: about 1.6). Before the fix this peaked at 3 (JPEG) to 4 (RGBA PNG) times
    the decoded size.
    """
    import os
    import subprocess
    import sys

    path = tmp_path / name
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated: the orientation must not cost a full-size copy
    Image.new(mode, (5000, 5000), (10, 120, 200, 255)[: len(mode)]).save(
        path, format=fmt, exif=exif, icc_profile=_srgb_bytes()
    )
    result = subprocess.run(  # noqa: S603 (fixed script, test-controlled arguments)
        [sys.executable, "-c", PEAK_SCRIPT, str(path), name],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[4],
        # A fixed mmap threshold makes glibc return each freed image block at once, so the
        # resident peak is the live peak and does not depend on earlier allocations.
        env={**os.environ, "MALLOC_MMAP_THRESHOLD_": "131072"},
    )
    peak, width, height = (int(value) for value in result.stdout.split())
    assert width * height <= 10_000_000
    decoded = 5000 * 5000 * 4
    print(f"peak {peak / 2**20:.0f} MiB for {decoded / 2**20:.0f} MiB decoded")
    assert peak <= 1.8 * decoded, f"peak {peak / 2**20:.0f} MiB"


# --- SVG or raster, by content (THM.12 with SEC.2) -------------------------------------------

LOGO_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect/></svg>'


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("logo.svg", LOGO_SVG),
        ("LOGO.SVG", b"\xef\xbb\xbf\n  " + LOGO_SVG),  # a byte order mark and blank lines
        ("logo.svg", SVG_WITH_SCRIPT),  # markup named .svg: the sanitiser decides
    ],
)
def test_markup_named_svg_goes_to_the_svg_sanitiser(name: str, data: bytes) -> None:
    assert upload_kind(data, name) == "svg"


@pytest.mark.parametrize("name", ["lake.png", "lake.svg", "lake.jpg", "lake"])
def test_a_raster_image_goes_to_the_raster_checks_whatever_its_name(name: str) -> None:
    assert upload_kind(png(), name) == "raster"


def test_a_png_named_svg_is_refused_by_the_raster_checks_for_its_name() -> None:
    with pytest.raises(UploadRefused) as refusal:
        sanitise_image(png(), "logo.svg", LIMITS)
    assert refusal.value.code == "mismatch"
    assert refusal.value.message == "The file name says SVG but the content is PNG."


@pytest.mark.parametrize("name", ["logo.png", "logo.jpg", "logo", "logo.svg.png"])
def test_an_svg_under_another_name_is_refused(name: str) -> None:
    with pytest.raises(UploadRefused) as refusal:
        upload_kind(LOGO_SVG, name)
    assert refusal.value.code == "svg_misnamed"


def test_other_markup_is_never_an_image() -> None:
    with pytest.raises(UploadRefused) as refusal:
        upload_kind(b"<!DOCTYPE html><html></html>", "page.png")
    assert refusal.value.code == "invalid"
