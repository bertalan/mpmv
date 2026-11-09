"""Image files under MEDIA_ROOT, served same-origin as images (THM.12, spec 14 §14.7).

Only Wagtail's image folders and image types are served; anything else is 404, so an uploaded
file can never come back as HTML. Every response is `Content-Disposition: inline` with
`nosniff` and its own Content-Security-Policy, which the CSP middleware leaves in place:
`default-src 'none'; sandbox`, plus `style-src 'unsafe-inline'` for SVG (the only thing a
logo may use, see `apps.core.svg`), so an SVG opened on its own runs nothing and loads
nothing.

On STAGE and LIVE, Caddy serves MEDIA_ROOT itself (deploy/caddy/Caddyfile, ARC.13) with the same
folders, extensions and headers; tests/unit/test_quadlets.py checks that the two stay in step
with the constants below.
"""

import posixpath
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpRequest
from django.utils._os import safe_join
from django.views.decorators.http import require_GET

MEDIA_FOLDERS = ("original_images/", "images/")
MEDIA_TYPES = {
    ".avif": "image/avif",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
RASTER_CSP = "default-src 'none'; sandbox"
CACHE_CONTROL = "public, max-age=86400"


def _image_file(path: str) -> Path:
    """The file `path` names, if it is inside one of the image folders; 404 otherwise.

    `path` is already percent-decoded, so `%2e%2e` and `%2f` arrive as `..` and `/`. Any `.` or
    `..` segment (or a backslash, or a NUL byte, which the filesystem calls reject with a
    ValueError) is refused before the folder is looked at, and the resolved
    file must still lie inside that folder: `images/../documents/x.png` stays inside MEDIA_ROOT
    but reaches Wagtail documents, which only their permission-checked view may serve.
    """
    segments = path.split("/")
    if "\\" in path or "\x00" in path or any(segment in ("", ".", "..") for segment in segments):
        raise Http404("not an image")
    folder = segments[0] + "/"
    if folder not in MEDIA_FOLDERS or len(segments) < 2:
        raise Http404("not an image")
    media_root = Path(settings.MEDIA_ROOT).resolve()
    try:
        full_path = Path(safe_join(media_root, path)).resolve()
    except (SuspiciousFileOperation, ValueError) as error:
        raise Http404("not an image") from error
    if not full_path.is_relative_to((media_root / folder).resolve()) or not full_path.is_file():
        raise Http404("not an image")
    return full_path


@require_GET
def serve_media(request: HttpRequest, path: str) -> FileResponse:
    """An uploaded image or rendition, as an image and never as HTML."""
    extension = posixpath.splitext(path)[1].lower()
    content_type = MEDIA_TYPES.get(extension)
    if content_type is None:
        raise Http404("not an image")
    full_path = _image_file(path)
    response = FileResponse(full_path.open("rb"), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = SVG_CSP if extension == ".svg" else RASTER_CSP
    response["Cache-Control"] = CACHE_CONTROL
    return response
