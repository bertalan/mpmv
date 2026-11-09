"""SEC.2: the upload checks and the image re-encoding, run on the malicious-upload corpus.

`tests/upload_corpus.py` builds the files; `tests/integration/test_upload_paths.py` runs the same
cases through every upload path of the site.
"""

import io

import pytest
from PIL import Image

from apps.core.uploads import (
    ImageLimits,
    UploadRefused,
    check_document,
    extension_of,
    safe_filename,
    sanitise_image,
)
from tests.upload_corpus import (
    Case,
    assert_safe_document,
    assert_safe_image,
    document_cases,
    image_cases,
    name_cases,
)

LIMITS = ImageLimits(max_bytes=10 * 1024 * 1024, max_pixels=40_000_000, max_side=16_000)
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
