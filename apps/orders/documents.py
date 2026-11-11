"""The per-vendor non-fiscal order confirmation PDF (ORD.9 3/3, ADR-0010).

`render_vendor_confirmation_pdf(vendor_order)` is a pure function of the order snapshots: the
vendor's name when ordered, its own lines, its shipping and its total. It is rendered when the
confirmation email is sent (in the queue worker, never in a request) and stored nowhere. The
platform never issues invoices or receipts, so the document says "non-fiscal" in the order's
language and carries the order reference, never an invoice number.

WeasyPrint is the only renderer and `render_html` the only place it is called: the HTML is
rendered with `StaticOnlyFetcher`, which loads `data:` URIs and files under the static roots and
nothing else. There is no network access, so no request forgery through `url()`, `<link>` or
`<img>`, and no redirect to follow. The template is autoescaped and loads no resource at all;
`base_url` is a fixed internal value. WeasyPrint is imported on first use, because the build
stage and the web process do not need its system libraries.
"""

import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from django.apps import apps as django_apps
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from apps.core import mail
from apps.core.services import AttachmentUnavailable, email_language

from .models import PrivacyStatus, VendorOrder
from .notifications import CONFIRMATION_PDF_KIND, _lines, _money, _vendor_name

logger = logging.getLogger(__name__)

ATTACHMENT_KIND = CONFIRMATION_PDF_KIND
TEMPLATE = "documents/orders/vendor_confirmation.html"
#: Not a real address: relative references in the document resolve to nothing.
BASE_URL = "mpmv-document:///"
#: The PDF is 1 to 3 pages; anything bigger or slower is a fault, and the email goes without it.
MAX_PDF_BYTES = 1_048_576
MAX_RENDER_SECONDS = 10.0


class ConfirmationUnavailable(AttachmentUnavailable):
    """No document for this order: it is not `active` any more (D-30) or does not exist."""


class ForbiddenResource(ValueError):
    """The document asked for a resource that only static files and `data:` URIs may provide."""


def static_roots() -> tuple[Path, ...]:
    """The directories a document may read files from: `STATIC_ROOT`, `STATICFILES_DIRS` and
    each installed app's `static/`. Never the media root or the source tree as a whole."""
    candidates: list[Path] = []
    if settings.STATIC_ROOT:
        candidates.append(Path(settings.STATIC_ROOT))
    for entry in settings.STATICFILES_DIRS:
        candidates.append(Path(entry[1] if isinstance(entry, tuple) else entry))
    candidates += [Path(config.path) / "static" for config in django_apps.get_app_configs()]
    roots = {path.resolve() for path in candidates if path.is_dir()}
    return tuple(sorted(roots))


class StaticOnlyFetcher:
    """A WeasyPrint URL fetcher that allows `data:` URIs and files under `roots` only.

    `file:` paths are resolved with `realpath` before the check, so `..`, encoded dots and
    symlinks cannot leave the roots. Every other scheme, and `file:` with a remote host, raises
    `ForbiddenResource` before anything is opened. Redirects are never followed: no HTTP request
    is ever made. It wraps WeasyPrint's own fetcher, built on first use (WeasyPrint is imported
    lazily), which only ever sees `data:` and checked `file:` URLs.
    """

    _fail_on_errors = False  # read by WeasyPrint: a refused resource is logged, not fatal

    def __init__(self, roots: Iterable[Path] | None = None) -> None:
        source = static_roots() if roots is None else roots
        self._roots = tuple(Path(root).resolve() for root in source)
        self._wrapped: Any = None

    @property
    def _inner(self) -> Any:
        if self._wrapped is None:
            from weasyprint.urls import URLFetcher

            self._wrapped = URLFetcher(
                timeout=1, allowed_protocols={"data", "file"}, allow_redirects=False
            )
        return self._wrapped

    @property
    def handlers(self) -> list[Any]:
        return list(self._inner.handlers)

    def __call__(self, url: str) -> Any:
        return self.fetch(url)

    def fetch(self, url: str, headers: Any = None) -> Any:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme == "data":
            return self._inner.fetch(url, headers)
        if scheme != "file":
            raise ForbiddenResource(f"resource scheme {scheme or '(none)'!r} is not allowed")
        if parts.netloc not in ("", "localhost"):
            raise ForbiddenResource("file resources on another host are not allowed")
        path = Path(url2pathname(parts.path)).resolve()
        if not any(path.is_relative_to(root) for root in self._roots):
            raise ForbiddenResource("file resource is outside the static roots")
        return self._inner.fetch(path.as_uri(), headers)


def render_html(html: str) -> bytes:
    """Render `html` to a tagged PDF (PDF/UA-1 variant) through the locked-down fetcher. The one
    place WeasyPrint is called. The document language and title come from the HTML."""
    from weasyprint import HTML

    document = HTML(string=html, base_url=BASE_URL, url_fetcher=StaticOnlyFetcher())
    return bytes(document.write_pdf(pdf_variant="pdf/ua-1", pdf_tags=True))


def _context(sub: VendorOrder, language: str) -> dict[str, Any]:
    order = sub.order
    return {
        "email_language": language,
        "order_number": order.number,
        "vendor_name": _vendor_name(sub),
        "lines": _lines(sub),
        "shipping": _money(sub.shipping_net + sub.shipping_tax_amount),
        "total": _money(sub.gross_total),
    }


def render_vendor_confirmation_pdf(vendor_order: VendorOrder) -> bytes:
    """The non-fiscal confirmation of one vendor's part of an order, in the order's language
    (English when it is not configured). Raises `ConfirmationUnavailable` when the order is not
    `active` (nothing is produced for a restricted or anonymised order)."""
    order = vendor_order.order
    if order.privacy_status != PrivacyStatus.ACTIVE:
        raise ConfirmationUnavailable("order is not active")
    language = email_language(order.language)
    with translation.override(language):
        html = render_to_string(TEMPLATE, _context(vendor_order, language))
    return render_html(html)


def vendor_confirmation_attachment(ref: str) -> mail.Attachment:
    """The email attachment for the vendor order with public id `ref` (the outbox builds it when
    the email is sent). Raises `AttachmentUnavailable` when it cannot or may not be produced, or
    when the render is over the size or time cap; the email then goes out without it."""
    sub = (
        VendorOrder.objects.select_related("order", "vendor").filter(public_id=ref).first()
        if _is_uuid(ref)
        else None
    )
    if sub is None:
        raise ConfirmationUnavailable("vendor order not found")
    started = time.monotonic()
    pdf = render_vendor_confirmation_pdf(sub)
    elapsed = time.monotonic() - started
    if elapsed > MAX_RENDER_SECONDS:
        raise AttachmentUnavailable(f"PDF render took {elapsed:.1f} s (cap {MAX_RENDER_SECONDS} s)")
    if len(pdf) > MAX_PDF_BYTES:
        raise AttachmentUnavailable(f"PDF is {len(pdf)} bytes (cap {MAX_PDF_BYTES})")
    return mail.Attachment(
        filename=f"order-{sub.order.number}-{str(sub.public_id)[:8]}.pdf",
        content_type="application/pdf",
        content=pdf,
    )


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
