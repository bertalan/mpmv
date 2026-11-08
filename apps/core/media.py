"""Image files under MEDIA_ROOT, served same-origin as images (THM.12, spec 14 §14.7).

Only Wagtail's image folders and image types are served; anything else is 404, so an uploaded
file can never come back as HTML. Every response is `Content-Disposition: inline` with
`nosniff` and its own Content-Security-Policy, which the CSP middleware leaves in place:
`default-src 'none'; sandbox`, plus `style-src 'unsafe-inline'` for SVG (the only thing a
logo may use, see `apps.core.svg`), so an SVG opened on its own runs nothing and loads
nothing.

The app serves these until the reverse proxy serves MEDIA_ROOT with the same headers.
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


@require_GET
def serve_media(request: HttpRequest, path: str) -> FileResponse:
    """An uploaded image or rendition, as an image and never as HTML."""
    extension = posixpath.splitext(path)[1].lower()
    content_type = MEDIA_TYPES.get(extension)
    if content_type is None or not path.startswith(MEDIA_FOLDERS):
        raise Http404("not an image")
    try:
        full_path = Path(safe_join(settings.MEDIA_ROOT, path))
    except SuspiciousFileOperation as error:
        raise Http404("not an image") from error
    if not full_path.is_file():
        raise Http404("not an image")
    response = FileResponse(full_path.open("rb"), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = SVG_CSP if extension == ".svg" else RASTER_CSP
    response["Cache-Control"] = "public, max-age=86400"
    return response
