"""Malicious-upload corpus (SEC.2): small hostile files, generated in code, no binary blobs.

Every upload path runs the same cases (`tests/integration/test_upload_paths.py`), and the unit
tests of `apps.core.uploads` run them against the checks directly. A case is either refused or
stored safely; "safely" means (see `assert_safe_image`):

- the stored name is made by the app (ASCII, lowercase, one extension taken from the detected
  content, no path, at most 100 characters) and is one that Caddy serves as an inert image;
- the stored original is a re-encoding: the planted payloads (`payloads`) are gone, and so are
  EXIF (GPS included), XMP, comments and text chunks;
- its format matches its extension, and its pixels are within the caps.

Image cases come from `image_cases()`, file-name cases from `name_cases()`, document cases from
`document_cases()`. Generating them takes well under a second; the largest file is a few KB.
"""

import io
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image

Verdict = Literal["refuse", "store"]

# What a stored original's name may look like (Wagtail puts originals in `original_images/`).
# The storage may add `_` and 7 random letters or digits to keep a name unique.
UNIQUE_SUFFIX = r"(_[A-Za-z0-9]{7}){0,2}"
STORED_IMAGE_NAME = re.compile(
    rf"^original_images/[a-z0-9][a-z0-9_-]*{UNIQUE_SUFFIX}\.(jpg|png|gif|webp|avif)$"
)
STORED_DOCUMENT_NAME = re.compile(rf"^documents/[a-z0-9][a-z0-9_-]*{UNIQUE_SUFFIX}\.pdf$")
# Caddy's public media matcher (deploy/caddy/Caddyfile, `@public_media`).
CADDY_PUBLIC_MEDIA = re.compile(
    r"^/media/(original_images|images)/[^/]+\.(?i:avif|webp|jpe?g|png|gif|svg)$"
)

SCRIPT = b"<script>alert(document.domain)</script>"
HTML_PAGE = b"<!DOCTYPE html><html><body>" + SCRIPT + b"</body></html>"
PHP_SHELL = b"<?php echo shell_exec($_GET['c']); ?>"
SVG_WITH_SCRIPT = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">' + SCRIPT + b"</svg>"
)
SECRET_PLACE = b"Secret Street 1, Hometown"  # planted in metadata: must not survive
RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"  # shows the rest of a name reversed: "x{RLO}gnp.exe"


@dataclass(frozen=True)
class Case:
    id: str
    filename: str
    data: bytes
    verdict: Verdict
    why: str
    payloads: tuple[bytes, ...] = ()  # bytes that must not survive in a stored file
    expect_stem: str | None = None  # the stored name's stem, when the case is about names
    expect_size: tuple[int, int] | None = None  # width, height after re-encoding
    xfail: str | None = None  # a protection that is not there yet (strict xfail)
    skip: str | None = None
    tags: frozenset[str] = field(default_factory=frozenset)


# --- Builders -------------------------------------------------------------------------------


def _image(
    fmt: str, size: tuple[int, int] = (24, 16), mode: str = "RGB", **params: object
) -> bytes:
    buffer = io.BytesIO()
    colour: object = (200, 40, 40) if mode in {"RGB", "RGBA"} else 128
    Image.new(mode, size, colour).save(buffer, format=fmt, **params)  # type: ignore[arg-type]
    return buffer.getvalue()


def png(size: tuple[int, int] = (24, 16)) -> bytes:
    return _image("PNG", size)


def _chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def _raw_png(width: int, height: int, idat: bytes, *, extra: bytes = b"", depth: int = 8) -> bytes:
    """A PNG written chunk by chunk: the header can claim any size, whatever the data holds."""
    header = struct.pack(">IIBBBBB", width, height, depth, 0, 0, 0, 0)  # greyscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + extra
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def _zeros_compressed(total: int, row: int) -> bytes:
    compressor = zlib.compressobj(9)
    out = [compressor.compress(b"\x00" * row) for _ in range(total // row)]
    out.append(compressor.flush())
    return b"".join(out)


def _exif(orientation: int | None = None) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"  # Make
    exif[0x0110] = "CameraModel"  # Model
    exif[0x013B] = SECRET_PLACE.decode()  # Artist
    if orientation is not None:
        exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)  # GPSInfo
    gps[1], gps[2] = "N", (45.0, 4.0, 12.5)
    gps[3], gps[4] = "E", (7.0, 41.0, 3.2)
    return exif.tobytes()


XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:Description>'
    + SECRET_PLACE
    + b"</rdf:Description></x:xmpmeta>"
)


def jpeg_with_gps(size: tuple[int, int] = (32, 20), orientation: int | None = None) -> bytes:
    return _image("JPEG", size, exif=_exif(orientation), xmp=XMP, comment=SECRET_PLACE, quality=90)


def _zip_archive() -> bytes:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("payload.html", HTML_PAGE)
    return buffer.getvalue()


def _gif_polyglot() -> bytes:
    """GIFAR-style: a valid GIF with a script in its comment block and HTML after the trailer."""
    return _image("GIF", (8, 8), mode="P", comment=b"*/=1;" + SCRIPT + b"/*") + HTML_PAGE


def _jpeg_html_polyglot() -> bytes:
    """A valid JPEG with HTML in a comment segment and after the end-of-image marker."""
    return _image("JPEG", (16, 16), comment=HTML_PAGE) + HTML_PAGE


def _animated_gif(frames: int, size: tuple[int, int]) -> bytes:
    # Grey frames that alternate black and white: Pillow merges consecutive identical frames
    # (two palette indices of a new "P" image look the same), so they must differ.
    images = [Image.new("L", size, (n % 2) * 255) for n in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=50)
    return buffer.getvalue()


def _mpo_photo() -> bytes:
    """A multi-picture JPEG (MPO), as some phones and cameras write: two images and an MP index."""
    first = Image.new("RGB", (24, 16), (200, 40, 40))
    second = Image.new("RGB", (24, 16), (40, 40, 200))
    buffer = io.BytesIO()
    first.save(buffer, format="MPO", save_all=True, append_images=[second], exif=_exif())
    return buffer.getvalue()


def _icc_with_payload() -> bytes:
    """An sRGB profile with attacker-chosen bytes after it: the profile must not survive as is."""
    from PIL import ImageCms

    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return srgb + SCRIPT + SECRET_PLACE


def _minimal_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()


PDF = _minimal_pdf()


# --- Cases ----------------------------------------------------------------------------------

LOGOS_NEED_151 = "SVG logos are THM.12 (PR #151), not on main yet"


def image_cases() -> list[Case]:
    zip_bytes = _zip_archive()
    return [
        Case("valid-png", "lake.png", png(), "store", "control: a plain PNG is stored"),
        Case("valid-jpeg", "lake.jpg", _image("JPEG"), "store", "control: a plain JPEG"),
        Case("valid-webp", "lake.webp", _image("WEBP"), "store", "control: a plain WebP"),
        Case("valid-gif", "lake.gif", _image("GIF", mode="P"), "store", "control: a plain GIF"),
        # Polyglots: valid images that also carry a second, active format.
        Case(
            "gif-js-polyglot",
            "pixel.gif",
            _gif_polyglot(),
            "store",
            "GIF+JS/HTML polyglot: re-encoding drops the comment and the trailing HTML",
            payloads=(SCRIPT, b"<html"),
            tags=frozenset({"polyglot"}),
        ),
        Case(
            "jpeg-html-polyglot",
            "photo.jpg",
            _jpeg_html_polyglot(),
            "store",
            "JPEG+HTML polyglot: re-encoding drops the comment and the bytes after EOI",
            payloads=(SCRIPT, b"<html"),
            tags=frozenset({"polyglot"}),
        ),
        Case(
            "png-zip-polyglot",
            "photo.png",
            png() + zip_bytes,
            "store",
            "PNG+ZIP polyglot: the appended archive does not survive re-encoding",
            payloads=(b"PK\x03\x04", b"payload.html"),
            tags=frozenset({"polyglot"}),
        ),
        # Wrong extension for the content.
        Case("html-as-jpg", "photo.jpg", HTML_PAGE, "refuse", "HTML named .jpg"),
        Case("svg-as-png", "logo.png", SVG_WITH_SCRIPT, "refuse", "SVG with a script named .png"),
        Case("php-as-jpg", "shell.jpg", PHP_SHELL, "refuse", "PHP source named .jpg"),
        Case("pdf-as-png", "scan.png", PDF, "refuse", "a PDF named .png"),
        Case("zip-as-gif", "anim.gif", zip_bytes, "refuse", "a ZIP archive named .gif"),
        Case(
            "png-as-jpg",
            "photo.jpg",
            png(),
            "refuse",
            "a PNG named .jpg: extension and content disagree",
        ),
        Case("bmp-as-png", "photo.png", _image("BMP"), "refuse", "BMP is not an accepted format"),
        Case(
            "tiff-as-jpg", "photo.jpg", _image("TIFF"), "refuse", "TIFF is not an accepted format"
        ),
        Case("truncated-png", "cut.png", png((64, 64))[:60], "refuse", "a truncated PNG"),
        Case("empty-file", "empty.png", b"", "refuse", "an empty file"),
        # SVG outside the logos collection.
        Case(
            "svg-plain", "logo.svg", SVG_WITH_SCRIPT, "refuse", "SVG outside the logos collection"
        ),
        # Oversized dimensions and decompression bombs.
        Case(
            "huge-dimensions-tiny-file",
            "bomb.png",
            _raw_png(100_000, 100_000, zlib.compress(b"\x00" * 64)),
            "refuse",
            "PNG header claims 10 gigapixels in under 100 bytes",
            tags=frozenset({"bomb"}),
        ),
        Case(
            "deeply-compressed-png",
            "bomb.png",
            _raw_png(8000, 8000, _zeros_compressed(8000 * 1001, 1001), depth=1),
            "refuse",
            "valid 1-bit PNG: 64 megapixels from about 8 KB of zlib",
            tags=frozenset({"bomb"}),
        ),
        Case(
            "too-wide",
            "strip.png",
            _image("PNG", (20_000, 1), mode="L"),
            "refuse",
            "20000 px wide: over the per-side cap, though few pixels",
            tags=frozenset({"bomb"}),
        ),
        Case(
            "gif-frame-bomb",
            "anim.gif",
            _animated_gif(frames=12, size=(2000, 2000)),
            "refuse",
            "12 frames of 2000x2000: 48 megapixels in all",
            tags=frozenset({"bomb"}),
        ),
        Case(
            "gif-many-frames",
            "anim.gif",
            _animated_gif(frames=1000, size=(2, 2)),
            "refuse",
            "1000 tiny frames: few pixels, but each frame costs CPU to decode and re-encode",
            tags=frozenset({"bomb"}),
        ),
        Case(
            "png-text-bomb",
            "note.png",
            _raw_png(
                1,
                1,
                zlib.compress(b"\x00\x00"),
                extra=_chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"\x00" * 20_000_000, 9)),
            ),
            "refuse",
            "1x1 PNG with a 20 MB compressed text chunk (about 20 KB on disk)",
            tags=frozenset({"bomb"}),
        ),
        # Metadata on originals.
        Case(
            "jpeg-exif-gps",
            "holiday.jpg",
            jpeg_with_gps(),
            "store",
            "EXIF with GPS, camera and artist, XMP and a comment: stripped from the original",
            payloads=(SECRET_PLACE, b"CameraMaker", b"Exif\x00\x00", b"xmpmeta"),
            tags=frozenset({"metadata"}),
        ),
        Case(
            "jpeg-exif-orientation",
            "rotated.jpg",
            jpeg_with_gps((32, 20), orientation=6),
            "store",
            "EXIF orientation 6: applied to the pixels, then the EXIF is dropped",
            payloads=(SECRET_PLACE, b"Exif\x00\x00"),
            expect_size=(20, 32),
            tags=frozenset({"metadata"}),
        ),
        Case(
            "png-exif-text",
            "map.png",
            _image("PNG", exif=_exif(), pnginfo=_png_text()),
            "store",
            "PNG with an eXIf chunk and tEXt/iTXt chunks",
            payloads=(SECRET_PLACE, b"eXIf", b"tEXt", b"iTXt"),
            tags=frozenset({"metadata"}),
        ),
        Case(
            "webp-exif-xmp",
            "map.webp",
            _image("WEBP", exif=_exif(), xmp=XMP),
            "store",
            "WebP with EXIF and XMP chunks",
            payloads=(SECRET_PLACE, b"EXIF", b"XMP "),
            tags=frozenset({"metadata"}),
        ),
        Case(
            "mpo-photo",
            "phone.jpg",
            _mpo_photo(),
            "store",
            "multi-picture JPEG from a phone: stored as a single-frame JPEG, index and EXIF gone",
            payloads=(b"MPF\x00", SECRET_PLACE, b"Exif\x00\x00"),
            tags=frozenset({"metadata"}),
        ),
        Case(
            "jpeg-icc-payload",
            "print.jpg",
            _image("JPEG", icc_profile=_icc_with_payload()),
            "store",
            "ICC profile carrying extra bytes: converted to sRGB, the profile is not kept",
            payloads=(SCRIPT, SECRET_PLACE),
            tags=frozenset({"metadata"}),
        ),
        Case(
            "png-icc-payload",
            "print.png",
            _image("PNG", icc_profile=_icc_with_payload()),
            "store",
            "PNG iCCP chunk carrying extra bytes: converted to sRGB, the profile is not kept",
            payloads=(SCRIPT, SECRET_PLACE),
            tags=frozenset({"metadata"}),
        ),
    ]


def _png_text() -> object:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Location", SECRET_PLACE.decode())
    info.add_itxt("Comment", SECRET_PLACE.decode() + " " + SCRIPT.decode())
    return info


def name_cases() -> list[Case]:
    """File names: refused when the extension is not an image one, otherwise rewritten."""
    data = png()
    refuse = [
        ("php", "shell.php"),
        ("exe", "setup.exe"),
        ("html", "page.html"),
        ("htaccess", ".htaccess"),
        ("double-extension-exe", "photo.png.exe"),
        ("rtl-override", f"invoice{RLO}gnp.exe"),  # shows as "invoiceexe.png"
        ("no-extension", "photo"),
        ("svg", "logo.svg"),
        ("only-extension", ".png"),  # a hidden file without extension
    ]
    store = [
        ("double-extension-php", "photo.php.png", "photo-php"),
        ("nul-byte", "photo\x00.php.png", "photo-php"),
        ("path-traversal", "../../etc/passwd.png", "passwd"),
        ("windows-traversal", "..\\..\\windows\\win.png", "win"),
        ("reserved-con", "CON.png", "image-con"),
        ("reserved-com1", "com1.png", "image-com1"),
        ("long-name", ("a" * 300) + ".png", "a" * 50),
        ("rtl-override-kept-ext", f"report{RLO}gnp.png", "reportgnp"),
        ("unicode", "Fötö été 😀.png", None),
        ("upper-case", "LAKE.PNG", "lake"),
        ("only-symbols", "~~~.png", None),
        ("spaces-and-shell", "a b;rm -rf $(x).png", None),
    ]
    return [
        Case(f"name-{key}", name, data, "refuse", f"refused name {name!r}") for key, name in refuse
    ] + [
        Case(f"name-{key}", name, data, "store", f"rewritten name {name!r}", expect_stem=stem)
        for key, name, stem in store
    ]


def document_cases() -> list[Case]:
    zip_bytes = _zip_archive()
    return [
        Case("doc-valid-pdf", "guide.pdf", PDF, "store", "control: a plain PDF"),
        Case(
            "doc-pdf-zip-polyglot",
            "guide.pdf",
            PDF + zip_bytes,
            "refuse",
            "PDF+ZIP polyglot (archive after %%EOF)",
            tags=frozenset({"polyglot"}),
        ),
        Case(
            "doc-zip-in-pdf",
            "guide.pdf",
            PDF[: -len(b"%%EOF\n")] + zip_bytes + b"%%EOF\n",
            "refuse",
            "ZIP archive hidden before the final %%EOF",
            tags=frozenset({"polyglot"}),
        ),
        Case("doc-html-as-pdf", "guide.pdf", HTML_PAGE, "refuse", "HTML named .pdf"),
        Case(
            "doc-html-pdf-polyglot",
            "guide.pdf",
            b"%PDF-1.4\n" + HTML_PAGE + PDF[9:],
            "refuse",
            "PDF header followed by HTML a browser would sniff",
            tags=frozenset({"polyglot"}),
        ),
        Case("doc-png-as-pdf", "guide.pdf", png(), "refuse", "an image named .pdf"),
        Case("doc-html", "guide.html", HTML_PAGE, "refuse", "an HTML document"),
        Case("doc-svg", "logo.svg", SVG_WITH_SCRIPT, "refuse", "an SVG document"),
        Case(
            "doc-exe", "setup.exe", b"MZ\x90\x00" + b"\x00" * 60, "refuse", "a Windows executable"
        ),
        Case("doc-php", "shell.php", PHP_SHELL, "refuse", "PHP source"),
        Case("doc-double-extension", "guide.pdf.exe", PDF, "refuse", "a .pdf.exe name"),
        Case(
            "doc-path-traversal",
            "../../guide.pdf",
            PDF,
            "store",
            "path traversal in the name",
            expect_stem="guide",
        ),
        Case(
            "doc-rtl-override",
            f"report{RLO}fdp.exe",
            PDF,
            "refuse",
            "RTL override hiding a .exe extension",
        ),
    ]


# --- Assertions on a stored file -------------------------------------------------------------

EXTENSION_FORMAT = {"jpg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WEBP", "avif": "AVIF"}
METADATA_INFO_KEYS = {
    "exif",
    "xmp",
    "XML:com.adobe.xmp",
    "comment",
    "Location",
    "Comment",
    "icc_profile",
}


def assert_safe_image(name: str, data: bytes, case: Case) -> None:
    """`name` (storage name) and `data` (stored bytes) of an accepted image are safe."""
    assert STORED_IMAGE_NAME.match(name), name
    assert len(name) <= 100, name
    assert CADDY_PUBLIC_MEDIA.match(f"/media/{name}"), name
    stem, extension = name.rsplit("/", 1)[1].rsplit(".", 1)
    if case.expect_stem is not None:
        assert re.fullmatch(re.escape(case.expect_stem) + UNIQUE_SUFFIX, stem), stem
    for payload in case.payloads:
        assert payload not in data, f"{case.id}: {payload!r} survived in the stored original"
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == EXTENSION_FORMAT[extension]
        assert not METADATA_INFO_KEYS & set(image.info), set(image.info)
        assert not image.getexif(), dict(image.getexif())
        if case.expect_size is not None:
            assert image.size == case.expect_size


def assert_safe_document(name: str, data: bytes, case: Case) -> None:
    """An accepted document keeps its bytes (it is checked, not rewritten) under a safe name."""
    assert STORED_DOCUMENT_NAME.match(name), name
    if case.expect_stem is not None:
        stem = name.rsplit("/", 1)[1].rsplit(".", 1)[0]
        assert re.fullmatch(re.escape(case.expect_stem) + UNIQUE_SUFFIX, stem), stem
    assert data == case.data
