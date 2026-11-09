"""Upload checks, safe file names and image re-encoding (SEC.2; spec 12 §12.3, requirement SEC.4).

Pure functions on bytes: no ORM, no storage. The adapters are `MediaImage` (its `clean` and the
file field that stores originals, `apps.core.models`) and the Wagtail document form
(`apps.core.document_forms`), so every path that writes an upload goes through here: the Wagtail
admin (single, multiple and chooser uploads, file replacement), `services.save_image` (imports,
vendor area) and the seed.

Images:

- the format comes from the content (Pillow, restricted to the accepted formats), never from the
  name, and the name's extension must agree with it;
- the size, the number of frames, the pixels of all frames together and each side are capped
  *before* decoding (a GIF's frame headers are walked one past the frame cap at most), and
  Pillow's decompression-bomb warning is an error (its threshold is the pixel cap, set once at
  startup by `configure_pillow`);
- an original over the stored-pixel cap (all frames together) is downscaled while re-encoded,
  keeping its aspect ratio, so the stored original fits that cap;
- the original is re-encoded: whatever is not pixels (EXIF and GPS, XMP, comments, text chunks,
  bytes after the end of the image, a second format in a polyglot) is not copied. The EXIF
  orientation is applied to the pixels first, so a photo keeps its orientation. An ICC colour
  profile is applied to the pixels (converted to sRGB) and not stored, so a profile cannot carry
  extra bytes; a profile that is too large or unreadable is dropped;
- a multi-picture JPEG (MPO, from some phones and cameras) is a JPEG: only its first picture is
  counted, decoded and kept;
- memory: the file is opened once and each step releases the image before it, so a still image
  costs its decoded original plus the stored copy. It is downscaled first, band by band (no
  full-size intermediate), then turned and converted to sRGB; a JPEG is decoded at a reduced
  scale when the stored size allows it;
- the stored name is rebuilt: ASCII, lowercase, no path, no second extension, no control or
  bidirectional characters, no Windows device name, the extension of the detected format.

SVG (THM.12) does not go through the raster checks: `upload_kind` sends an upload whose content
is markup to `apps.core.svg` (sanitised and re-serialised, in an SVG collection only) and every
other upload here. The branch comes from the content: an SVG must be named `.svg`, and a raster
image named `.svg` is checked as the raster image it is (and refused, its name disagreeing).

Documents (Wagtail documents, installed for wagtail-localize): PDF only, checked by content, with
no second format a browser or an unzip tool would find.
"""

import io
import math
import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass
from typing import Literal

from PIL import GifImagePlugin, Image, ImageCms, JpegImagePlugin

# Format (as Pillow names it) → stored extension; and the extensions accepted for each format.
IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "AVIF": "avif"}
IMAGE_EXTENSIONS = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "gif": "GIF",
    "webp": "WEBP",
    "avif": "AVIF",
}
# Formats Pillow reports for content it reads with an accepted format's plugin.
SAME_AS = {"MPO": "JPEG"}  # a JPEG with a multi-picture index (phones, cameras)
ANIMATED_FORMATS = frozenset({"GIF", "WEBP", "AVIF", "PNG"})
DOCUMENT_EXTENSIONS = ("pdf",)

# Image metadata kept by the re-encoding: what the pixels need to look right, nothing personal.
# The ICC profile is applied to the pixels instead (`_ColourConverter`).
KEPT_INFO = ("transparency", "duration", "loop", "background")
MAX_ICC_BYTES = 64 * 1024  # display profiles are a few KB; larger ones are dropped unread
ICC_MODES = {"RGB": "RGB", "RGBA": "RGBA", "CMYK": "RGB"}  # source mode → mode after conversion

WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"{p}{n}" for p in ("com", "lpt") for n in range(10)}
)
MAX_STEM = 50
FALLBACK_STEM = "image"


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int  # settings.WAGTAILIMAGES_MAX_UPLOAD_SIZE
    max_pixels: int  # decoded, all frames together; settings.WAGTAILIMAGES_MAX_IMAGE_PIXELS
    max_side: int  # settings.MPMV_MAX_IMAGE_SIDE
    max_frames: int = 300  # settings.MPMV_MAX_IMAGE_FRAMES
    max_stored_pixels: int = 40_000_000  # stored original; settings.MPMV_MAX_STORED_IMAGE_PIXELS


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    name: str  # the safe file name, extension included
    format: str
    width: int
    height: int


class UploadRefused(ValueError):
    """An upload that is not accepted; `message` is meant for the person who uploaded it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- Names -----------------------------------------------------------------------------------


def _plain(name: str) -> str:
    """The last path segment, without control, format (bidi) or combining characters."""
    base = re.split(r"[\\/]", name)[-1]
    base = unicodedata.normalize("NFKD", base)
    return "".join(ch for ch in base if unicodedata.category(ch)[0] not in {"C", "M"})


def extension_of(name: str) -> str:
    """The extension a browser or a file manager would see, lowercase ('' if none)."""
    base = _plain(name).strip().rstrip(".")
    stem, dot, extension = base.rpartition(".")
    return extension.lower() if dot and stem.strip(".") else ""


def safe_stem(name: str) -> str:
    """A storage-safe stem: ASCII letters, digits, `-` and `_`; every dot becomes a dash."""
    base = _plain(name)
    stem = base.rpartition(".")[0] if extension_of(name) else base
    ascii_stem = stem.encode("ascii", "ignore").decode()
    stem = re.sub(r"[^a-z0-9_]+", "-", ascii_stem.lower()).strip("-_")[:MAX_STEM].strip("-_")
    if not stem:
        return FALLBACK_STEM
    if stem in WINDOWS_DEVICE_NAMES:
        return f"{FALLBACK_STEM}-{stem}"
    return stem


def safe_filename(name: str, extension: str) -> str:
    return f"{safe_stem(name)}.{extension}"


# --- Images ----------------------------------------------------------------------------------


SVG_EXTENSION = "svg"
# A UTF-8 byte order mark and white space, before the first tag.
_MARKUP_LEAD = b"\xef\xbb\xbf \t\r\n"
_SVG_TAG = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?svg[\s>/]")


def is_markup(data: bytes) -> bool:
    """Whether `data` starts like an XML document (an SVG, or HTML, or anything tagged).

    No raster format starts with `<`: their signatures are binary. So an upload that does is
    never a raster image, whatever its name says.
    """
    return data.lstrip(_MARKUP_LEAD).startswith(b"<")


def upload_kind(data: bytes, name: str) -> Literal["svg", "raster"]:
    """Which checks an uploaded image goes through, from its content (THM.12 with SEC.2).

    "svg": the content is markup and the name ends in `.svg` (`apps.core.svg` decides whether
    it is a safe SVG). "raster": anything else, a raster image named `.svg` included, which the
    raster checks refuse for its name. Markup under another name is refused here: an SVG
    renamed `.png` must not be stored, and HTML is never an image.
    """
    if not is_markup(data):
        return "raster"
    if extension_of(name) == SVG_EXTENSION:
        return "svg"
    if _SVG_TAG.search(data[:4096]):
        raise UploadRefused(
            "svg_misnamed", "The file is an SVG: an SVG logo must keep its .svg file name."
        )
    raise UploadRefused("invalid", "The file is not a valid image.")


def safe_svg_name(name: str) -> str:
    return safe_filename(name, SVG_EXTENSION)


def _refuse_name(name: str) -> str:
    extension = extension_of(name)
    if extension == SVG_EXTENSION:
        return "SVG"  # the content decides: a raster image here disagrees with its name
    if extension not in IMAGE_EXTENSIONS:
        accepted = ", ".join(sorted({e.upper() for e in IMAGE_FORMATS.values()}))
        raise UploadRefused(
            "extension", f"Not a supported image file name. Supported formats: {accepted}."
        )
    return IMAGE_EXTENSIONS[extension]


def _open(data: bytes, limits: ImageLimits) -> Image.Image:
    if not data:
        raise UploadRefused("empty", "The file is empty.")
    if len(data) > limits.max_bytes:
        raise UploadRefused("too_large", "The file is too large.")
    # Pillow's decompression-bomb warning is an error here. Its threshold, `MAX_IMAGE_PIXELS`, is
    # set once at startup to the decode cap (`configure_pillow`, from `CoreConfig.ready`), so it
    # refuses nothing the cap accepts. The cap itself is `_check_size`, which does not rely on it.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=list(IMAGE_FORMATS))
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise UploadRefused("too_many_pixels", "The image has too many pixels.") from exc
    except Exception as exc:  # Pillow raises many types for files it cannot read
        raise UploadRefused("invalid", "The file is not a valid image.") from exc
    return image


def configure_pillow(max_pixels: int) -> None:
    """Set Pillow's process-wide bomb threshold to the decode cap; called once at startup.

    Pillow warns above `MAX_IMAGE_PIXELS` (about 89 megapixels by default) and errors above
    twice that. Setting it at startup, rather than on the first upload, keeps every Pillow use of
    the process (renditions, imports) on the same threshold whatever ran before.
    """
    Image.MAX_IMAGE_PIXELS = max_pixels


def _format_of(image: Image.Image) -> str:
    """The accepted format of an opened image; an unexpected one is a refusal, never a crash."""
    fmt = SAME_AS.get(str(image.format), str(image.format))
    if fmt not in IMAGE_FORMATS:
        raise UploadRefused("invalid", "The file is not a supported image.")
    return fmt


def _frame_count(image: Image.Image, fmt: str, cap: int) -> int:
    """The number of frames, counted from the headers and at most `cap + 1`; no frame decoded.

    A JPEG (an MPO's other pictures included) is one frame: only its first picture is kept. A
    GIF has no frame count in its header, so its frame headers are walked, one past the cap at
    most; the other formats store the count.
    """
    if fmt == "JPEG":
        return 1
    if isinstance(image, GifImagePlugin.GifImageFile):
        count = cap + 1
        try:
            for frame in range(1, cap + 1):
                image._seek(frame, False)  # the header only, as Pillow's own `n_frames` does
        except EOFError:
            count = frame
        finally:
            image.seek(0)
        return count
    return int(getattr(image, "n_frames", 1))


def _check_size(image: Image.Image, fmt: str, limits: ImageLimits) -> int:
    """The number of frames to decode, once the caps are checked on the headers."""
    width, height = image.size
    if max(width, height) > limits.max_side:
        raise UploadRefused(
            "too_wide", f"The image is too large: at most {limits.max_side} px a side."
        )
    try:
        frames = _frame_count(image, fmt, limits.max_frames)
    except UploadRefused:
        raise
    except Exception as exc:  # a corrupt frame header
        raise UploadRefused("invalid", "The file is not a valid image.") from exc
    if frames > limits.max_frames:
        raise UploadRefused(
            "too_many_frames",
            f"The animation has too many frames: at most {limits.max_frames}.",
        )
    if width * height * frames > limits.max_pixels:
        raise UploadRefused("too_many_pixels", "The image has too many pixels.")
    return frames


def _open_checked(
    data: bytes, name: str, limits: ImageLimits, check_name: bool
) -> tuple[Image.Image, str, int]:
    """The opened image, its format and its frame count, every cap checked on the headers."""
    named_format = _refuse_name(name) if check_name else None
    image = _open(data, limits)
    try:
        fmt = _format_of(image)
        if named_format is not None and fmt != named_format:
            raise UploadRefused(
                "mismatch",
                f"The file name says {named_format} but the content is {fmt}.",
            )
        frames = _check_size(image, fmt, limits)
    except BaseException:
        image.close()
        raise
    return image, fmt, frames


def _load(image: Image.Image, frames: int) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            for index in range(frames):
                if index:
                    image.seek(index)
                image.load()
            if frames > 1:
                image.seek(0)
    except Exception as exc:  # truncated or corrupt data, a text bomb, an oversized chunk
        raise UploadRefused("invalid", "The file is not a valid image.") from exc


def check_image(data: bytes, name: str, limits: ImageLimits, *, check_name: bool = True) -> str:
    """The detected format of an acceptable image; raises `UploadRefused` otherwise.

    Decodes every frame that would be stored, so a file that only looks right in its header is
    refused too. `check_name=False` skips the name (a stored file whose name the storage has
    rewritten).
    """
    image, fmt, frames = _open_checked(data, name, limits, check_name)
    with image:
        _load(image, frames)
        return fmt


class _ColourConverter:
    """Applies an image's ICC profile to its pixels (to sRGB); one transform per profile and mode.

    A profile that is too large, unreadable or unusable is dropped and the pixels kept as they
    are; palette and grey images keep their pixels too.
    """

    def __init__(self) -> None:
        self._transforms: dict[tuple[bytes, str], ImageCms.ImageCmsTransform | None] = {}

    def _transform(self, profile: bytes, mode: str) -> ImageCms.ImageCmsTransform | None:
        key = (profile, mode)
        if key not in self._transforms:
            try:
                self._transforms[key] = ImageCms.buildTransform(
                    ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                    ImageCms.createProfile("sRGB"),
                    mode,
                    ICC_MODES[mode],
                )
            except Exception:  # an unreadable or unusable profile: dropped
                self._transforms[key] = None
        return self._transforms[key]

    def __call__(self, image: Image.Image, profile: object) -> Image.Image:
        """`image` in sRGB: converted in place when the mode stays, else a new image."""
        if not isinstance(profile, bytes) or not profile or len(profile) > MAX_ICC_BYTES:
            return image
        if image.mode not in ICC_MODES:
            return image
        transform = self._transform(profile, image.mode)
        if transform is None:
            return image
        try:
            if ICC_MODES[image.mode] == image.mode:
                ImageCms.applyTransform(image, transform, inPlace=True)
                return image
            converted = ImageCms.applyTransform(image, transform)
        except Exception:  # the pixels as they are
            return image
        return converted if converted is not None else image


def stored_size(width: int, height: int, frames: int, max_pixels: int) -> tuple[int, int]:
    """The frame size to store: as it is, or scaled down (same aspect) to fit `max_pixels`."""
    if width * height * frames <= max_pixels:
        return width, height
    scale = math.sqrt(max_pixels / (width * height * frames))
    new_width, new_height = max(1, int(width * scale)), max(1, int(height * scale))
    while new_width * new_height * frames > max_pixels and min(new_width, new_height) > 1:
        new_width, new_height = (
            (new_width - 1, new_height) if new_width >= new_height else (new_width, new_height - 1)
        )
    return new_width, new_height


RESIZE_BAND = 256  # output rows per resampling pass
LANCZOS_SUPPORT = 3  # source rows on each side of an output row, per unit of scale
REDUCING_GAP = 3.0  # integer pre-reduction for large factors; Lanczos does the last step


def _resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """`image` resampled (Lanczos) to `size`, band by band: no full-size intermediate.

    Pillow resamples in two passes through an intermediate image of the target width and the
    source height, and resamples RGBA through a premultiplied copy of the whole source. Each
    band here crops the source rows its output rows need (with the filter's margin) and
    resamples only those, so the extra memory is a band, not a second copy of the original.
    """
    if image.mode in ("P", "PA"):  # nearest neighbour: Pillow makes no intermediate copy
        return image.resize(size, Image.Resampling.NEAREST)
    width, height = image.size
    out_width, out_height = size
    scale = height / out_height
    margin = math.ceil(LANCZOS_SUPPORT * max(scale, 1.0)) + 2
    output = Image.new(image.mode, size)
    for top in range(0, out_height, RESIZE_BAND):
        bottom = min(out_height, top + RESIZE_BAND)
        y0, y1 = top * scale, bottom * scale
        crop_top = max(0, math.floor(y0) - margin)
        crop_bottom = min(height, math.ceil(y1) + margin)
        band = image.crop((0, crop_top, width, crop_bottom))
        part = band.resize(
            (out_width, bottom - top),
            Image.Resampling.LANCZOS,
            box=(0, y0 - crop_top, width, y1 - crop_top),
            reducing_gap=REDUCING_GAP,
        )
        output.paste(part, (0, top))
    return output


def _replace(old: Image.Image, new: Image.Image) -> Image.Image:
    """`new`, with `old`'s pixels released at once when it is a different image."""
    if new is not old:
        old.close()
    return new


ORIENTATIONS = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
QUARTER_TURNS = frozenset({5, 6, 7, 8})  # orientations that swap width and height


def _encode_params(
    fmt: str, data: bytes, jpeg_tables: dict[str, object] | None
) -> dict[str, object]:
    params: dict[str, object] = {}
    if fmt == "JPEG":
        if jpeg_tables is not None:
            params.update(jpeg_tables)  # the source's own tables: no visible loss
        else:
            params["quality"] = 92
    elif fmt == "WEBP":
        params["lossless"] = b"VP8L" in data[:4096]
        params["quality"] = 92
    elif fmt == "AVIF":
        params["quality"] = 92
    return params


def _still(source: Image.Image, fmt: str, data: bytes, limits: ImageLimits) -> CleanImage:
    """One picture re-encoded, at most the decoded original and the stored copy in memory.

    Every step runs on the smallest image it can: the picture is downscaled first (a JPEG is
    decoded at a reduced scale when that is enough), then turned to its EXIF orientation and
    converted to sRGB; each step releases the image before it.
    """
    if fmt != "JPEG":
        _load(source, 1)  # PNG and WebP may keep their EXIF after the pixels
    try:
        orientation = source.getexif().get(0x0112, 1)
    except Exception:  # unreadable EXIF: no orientation
        orientation = 1
    method = ORIENTATIONS.get(orientation) if isinstance(orientation, int) else None
    turned = orientation in QUARTER_TURNS
    width, height = source.size
    shown = (height, width) if turned else (width, height)
    out_width, out_height = stored_size(*shown, 1, limits.max_stored_pixels)
    target = (out_height, out_width) if turned else (out_width, out_height)
    kept = {key: source.info[key] for key in KEPT_INFO if key in source.info}
    profile = source.info.get("icc_profile")
    mode = source.mode
    jpeg_tables: dict[str, object] | None = None
    if fmt == "JPEG":
        if target == source.size:
            jpeg_tables = {
                "qtables": source.quantization,  # type: ignore[attr-defined]
                "subsampling": JpegImagePlugin.get_sampling(source),
            }
        else:
            source.draft(None, target)  # DCT scaling (1/2, 1/4, 1/8) when the target allows
        _load(source, 1)

    image = _replace(source, _resize(source, target)) if source.size != target else source
    if method is not None:
        image = _replace(image, image.transpose(method))
    image = _replace(image, _ColourConverter()(image, profile))
    if image.mode != mode:
        jpeg_tables = None
    image.info.clear()
    image.info.update(kept)
    buffer = io.BytesIO()
    try:
        image.save(buffer, format=fmt, **_encode_params(fmt, data, jpeg_tables))
    except Exception as exc:  # e.g. a mode the format cannot write
        raise UploadRefused("invalid", "The image could not be processed.") from exc
    finally:
        image.close()
    return CleanImage(
        data=buffer.getvalue(), name="", format=fmt, width=out_width, height=out_height
    )


def _animation(source: Image.Image, fmt: str, frames: int, limits: ImageLimits) -> CleanImage:
    """Every frame decoded once, downscaled and converted, then the animation re-encoded."""
    size = stored_size(*source.size, frames, limits.max_stored_pixels)
    to_srgb = _ColourConverter()
    sequence: list[Image.Image] = []
    durations: list[int] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            for index in range(frames):
                source.seek(index)
                source.load()
                kept = {key: source.info[key] for key in KEPT_INFO if key in source.info}
                durations.append(source.info.get("duration", 100))
                frame = _resize(source, size) if source.size != size else source.copy()
                frame = _replace(frame, to_srgb(frame, source.info.get("icc_profile")))
                frame.info.clear()
                frame.info.update(kept)
                sequence.append(frame)
    except Exception as exc:  # truncated or corrupt data, a text bomb, an oversized chunk
        raise UploadRefused("invalid", "The file is not a valid image.") from exc
    loop = source.info.get("loop", 0)
    source.close()
    buffer = io.BytesIO()
    try:
        sequence[0].save(
            buffer,
            format=fmt,
            save_all=True,
            append_images=sequence[1:],
            duration=durations,
            loop=loop,
        )
    except Exception as exc:  # e.g. a mode the format cannot write
        raise UploadRefused("invalid", "The image could not be processed.") from exc
    return CleanImage(data=buffer.getvalue(), name="", format=fmt, width=size[0], height=size[1])


def sanitise_image(
    data: bytes, name: str, limits: ImageLimits, *, check_name: bool = True
) -> CleanImage:
    """Check `data` and re-encode it without its metadata; raises `UploadRefused`.

    The file is opened once: the caps are checked on its headers, then each frame kept is
    decoded once (the decode is also the check that the data is a valid image).
    """
    source, fmt, frames = _open_checked(data, name, limits, check_name)
    with source:
        if fmt in ANIMATED_FORMATS and frames > 1:
            clean = _animation(source, fmt, frames, limits)
        else:
            clean = _still(source, fmt, data, limits)
    return CleanImage(
        data=clean.data,
        name=safe_filename(name, IMAGE_FORMATS[fmt]),
        format=fmt,
        width=clean.width,
        height=clean.height,
    )


# --- Documents -------------------------------------------------------------------------------

PDF_MAGIC = b"%PDF-"
SNIFFABLE = re.compile(rb"<\s*(html|script|body|head|iframe|svg|!doctype)", re.IGNORECASE)
SNIFF_WINDOW = 1024  # browsers sniff the first 512 bytes; twice that for good measure
ZIP_END_RECORD = b"PK\x05\x06"
ZIP_END_WINDOW = 22 + 0xFFFF  # the record and the longest archive comment


def check_document(data: bytes, name: str, *, max_bytes: int) -> str:
    """The safe stored name of an acceptable PDF; raises `UploadRefused` otherwise."""
    if extension_of(name) not in DOCUMENT_EXTENSIONS:
        raise UploadRefused("extension", "Only PDF documents are accepted.")
    if not data:
        raise UploadRefused("empty", "The file is empty.")
    if len(data) > max_bytes:
        raise UploadRefused("too_large", "The file is too large.")
    if not data.startswith(PDF_MAGIC):
        raise UploadRefused("invalid", "The file is not a PDF.")
    if SNIFFABLE.search(data[:SNIFF_WINDOW]):
        raise UploadRefused("polyglot", "The file mixes a PDF with HTML.")
    # A ZIP reader looks for the end-of-central-directory record in the last 64 KiB.
    if zipfile.is_zipfile(io.BytesIO(data)) or ZIP_END_RECORD in data[-ZIP_END_WINDOW:]:
        raise UploadRefused("polyglot", "The file mixes a PDF with an archive.")
    if not data.rstrip().endswith(b"%%EOF"):
        raise UploadRefused("polyglot", "The file has data after the end of the PDF.")
    return safe_filename(name, "pdf")
