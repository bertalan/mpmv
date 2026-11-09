"""Upload checks, safe file names and image re-encoding (SEC.2; spec 12 §12.3, requirement SEC.4).

Pure functions on bytes: no ORM, no storage. The adapters are `MediaImage` (its `clean` and the
file field that stores originals, `apps.core.models`) and the Wagtail document form
(`apps.core.document_forms`), so every path that writes an upload goes through here: the Wagtail
admin (single, multiple and chooser uploads, file replacement), `services.save_image` (imports,
vendor area) and the seed.

Images:

- the format comes from the content (Pillow, restricted to the accepted formats), never from the
  name, and the name's extension must agree with it;
- the size, the pixels of all frames together and each side are capped *before* decoding, and
  Pillow's decompression-bomb warning is an error;
- the original is re-encoded: whatever is not pixels (EXIF and GPS, XMP, comments, text chunks,
  bytes after the end of the image, a second format in a polyglot) is not copied. The EXIF
  orientation is applied to the pixels first, so a photo keeps its orientation;
- the stored name is rebuilt: ASCII, lowercase, no path, no second extension, no control or
  bidirectional characters, no Windows device name, the extension of the detected format.

SVG is not accepted here: THM.12 adds sanitised SVG logos in one collection.

Documents (Wagtail documents, installed for wagtail-localize): PDF only, checked by content, with
no second format a browser or an unzip tool would find.
"""

import io
import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageSequence, JpegImagePlugin

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
DOCUMENT_EXTENSIONS = ("pdf",)

# Image metadata kept by the re-encoding: what the pixels need to look right, nothing personal.
KEPT_INFO = ("transparency", "duration", "loop", "background", "icc_profile")

WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"{p}{n}" for p in ("com", "lpt") for n in range(10)}
)
MAX_STEM = 50
FALLBACK_STEM = "image"


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int
    max_pixels: int  # all frames together
    max_side: int


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


def _refuse_name(name: str) -> str:
    extension = extension_of(name)
    if extension == "svg":
        raise UploadRefused("svg", "SVG images are not accepted here.")
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
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=list(IMAGE_FORMATS))
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise UploadRefused("too_many_pixels", "The image has too many pixels.") from exc
    except Exception as exc:  # Pillow raises many types for files it cannot read
        raise UploadRefused("invalid", "The file is not a valid image.") from exc
    return image


def _check_size(image: Image.Image, limits: ImageLimits) -> None:
    width, height = image.size
    if max(width, height) > limits.max_side:
        raise UploadRefused(
            "too_wide", f"The image is too large: at most {limits.max_side} px a side."
        )
    frames = getattr(image, "n_frames", 1)
    if width * height * frames > limits.max_pixels:
        raise UploadRefused("too_many_pixels", "The image has too many pixels.")


def _load(image: Image.Image) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            for frame in ImageSequence.Iterator(image):
                frame.load()
            image.seek(0)
    except Exception as exc:  # truncated or corrupt data, a text bomb, an oversized chunk
        raise UploadRefused("invalid", "The file is not a valid image.") from exc


def check_image(data: bytes, name: str, limits: ImageLimits, *, check_name: bool = True) -> str:
    """The detected format of an acceptable image; raises `UploadRefused` otherwise.

    Decodes every frame, so a file that only looks right in its header is refused too.
    `check_name=False` skips the name (a stored file whose name the storage has rewritten).
    """
    named_format = _refuse_name(name) if check_name else None
    image = _open(data, limits)
    with image:
        if named_format is not None and image.format != named_format:
            raise UploadRefused(
                "mismatch",
                f"The file name says {named_format} but the content is {image.format}.",
            )
        _check_size(image, limits)
        _load(image)
        return str(image.format)


def _strip(image: Image.Image) -> Image.Image:
    image.info = {key: image.info[key] for key in KEPT_INFO if key in image.info}
    return image


def _encode(image: Image.Image, fmt: str, source: Image.Image, data: bytes) -> bytes:
    buffer = io.BytesIO()
    frames = getattr(source, "n_frames", 1)
    if frames > 1 and fmt in {"GIF", "WEBP", "AVIF", "PNG"}:
        sequence = [_strip(frame.copy()) for frame in ImageSequence.Iterator(source)]
        durations = [frame.info.get("duration", 100) for frame in ImageSequence.Iterator(source)]
        sequence[0].save(
            buffer,
            format=fmt,
            save_all=True,
            append_images=sequence[1:],
            duration=durations,
            loop=source.info.get("loop", 0),
        )
        return buffer.getvalue()
    params: dict[str, object] = {}
    if fmt == "JPEG":
        if isinstance(source, JpegImagePlugin.JpegImageFile) and image.size == source.size:
            # The source's own quantisation tables: no visible loss from the re-encoding.
            params["qtables"] = source.quantization
            params["subsampling"] = JpegImagePlugin.get_sampling(source)
        else:
            params["quality"] = 92
    elif fmt == "WEBP":
        params["lossless"] = b"VP8L" in data[:4096]
        params["quality"] = 92
    elif fmt == "AVIF":
        params["quality"] = 92
    if "icc_profile" in image.info:
        params["icc_profile"] = image.info["icc_profile"]
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def sanitise_image(
    data: bytes, name: str, limits: ImageLimits, *, check_name: bool = True
) -> CleanImage:
    """Check `data` and re-encode it without its metadata; raises `UploadRefused`."""
    fmt = check_image(data, name, limits, check_name=check_name)
    with Image.open(io.BytesIO(data), formats=[fmt]) as source:
        image: Image.Image
        if getattr(source, "n_frames", 1) > 1:
            image = source  # animations keep their frames; EXIF orientation is a photo matter
        else:
            source.load()
            image = _strip(ImageOps.exif_transpose(source) or source.copy())
        try:
            encoded = _encode(image, fmt, source, data)
        except Exception as exc:  # e.g. a mode the format cannot write
            raise UploadRefused("invalid", "The image could not be processed.") from exc
        width, height = image.size
    return CleanImage(
        data=encoded,
        name=safe_filename(name, IMAGE_FORMATS[fmt]),
        format=fmt,
        width=width,
        height=height,
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
