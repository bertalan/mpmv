"""The per-vendor non-fiscal order confirmation PDF (ORD.9 3/3, ADR-0010).

`render_vendor_confirmation_pdf(vendor_order)` is a pure function of the order snapshots: the
vendor's name when ordered, its own lines, its shipping and its total. It is rendered when the
confirmation email is sent (in the queue worker, never in a request) and stored nowhere. The
platform never issues invoices or receipts, so the document says "non-fiscal" in the order's
language and carries the order reference, never an invoice number.

WeasyPrint runs only in `pdf_render` (no Django), in a child process with a hard timeout and no
application secrets; see there for the locked-down fetcher (`data:` URIs and static files only, no
network, no redirects). The template is autoescaped and loads no resource at all. WeasyPrint is
never imported in the worker itself, because the build stage and the web process do not need its
system libraries.
"""

import contextlib
import json
import logging
import math
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from django.apps import apps as django_apps
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from apps.core import mail
from apps.core.services import AttachmentUnavailable, email_language

from . import pdf_render
from .models import PrivacyStatus, VendorOrder
from .notifications import CONFIRMATION_PDF_KIND, _lines, _money, _vendor_name

logger = logging.getLogger(__name__)

ATTACHMENT_KIND = CONFIRMATION_PDF_KIND
TEMPLATE = "documents/orders/vendor_confirmation.html"
#: The PDF is 1 to 3 pages; anything bigger or slower is a fault, and the email goes without it.
MAX_PDF_BYTES = 1_048_576
MAX_RENDER_SECONDS = 10.0


class ConfirmationUnavailable(AttachmentUnavailable):
    """No document for this order: it is not `active` any more (D-30) or does not exist."""


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


def _renderer_command() -> list[str]:
    return [sys.executable, "-m", "apps.orders.pdf_render"]


#: What the renderer child inherits: enough to find fonts and temp space, no application secret.
_CHILD_ENV_KEYS = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
    "XDG_CACHE_HOME", "XDG_DATA_DIRS",
)  # fmt: skip


def _renderer_env() -> dict[str, str]:
    return {key: os.environ[key] for key in _CHILD_ENV_KEYS if key in os.environ}


def cpu_limit_seconds() -> int:
    """CPU seconds the child may use: a little above the wall-clock cap, as a second guard."""
    return math.ceil(MAX_RENDER_SECONDS) + 2


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the child and everything it started (it leads its own session) and reap it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.kill()
    process.wait()


def render_html(html: str) -> bytes:
    """Render `html` to a tagged PDF in a child process (`pdf_render`) that leads its own
    session, so a pathological layout cannot hang the worker: past `MAX_RENDER_SECONDS`, or as
    soon as its output passes `MAX_PDF_BYTES`, the whole process group is killed. The child also
    has an address-space and a CPU limit. Raises `AttachmentUnavailable` on a timeout, a crash
    or too much output; nothing but the output cap is ever buffered beyond it."""
    request = json.dumps(
        {
            "html": html,
            "roots": [str(root) for root in static_roots()],
            "memory_bytes": pdf_render.MEMORY_LIMIT_BYTES,
            "cpu_seconds": cpu_limit_seconds(),
        }
    )
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stderr:
        stdin.write(request.encode())
        stdin.seek(0)
        process = subprocess.Popen(  # noqa: S603 - fixed command, no shell, no user input in argv
            _renderer_command(),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=_renderer_env(),
            cwd=settings.BASE_DIR,
            start_new_session=True,
        )
        stdout = process.stdout
        if stdout is None:  # pragma: no cover - PIPE was requested
            raise AttachmentUnavailable("PDF renderer has no output")
        deadline = time.monotonic() + MAX_RENDER_SECONDS
        chunks: list[bytes] = []
        size = 0
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AttachmentUnavailable(
                        f"PDF render timed out after {MAX_RENDER_SECONDS} s"
                    )
                if not select.select([stdout], [], [], remaining)[0]:
                    continue
                chunk = os.read(stdout.fileno(), 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise AttachmentUnavailable(f"PDF is over the size cap ({MAX_PDF_BYTES} bytes)")
                chunks.append(chunk)
            try:
                code = process.wait(timeout=max(deadline - time.monotonic(), 0.1))
            except subprocess.TimeoutExpired as exc:
                raise AttachmentUnavailable(
                    f"PDF render timed out after {MAX_RENDER_SECONDS} s"
                ) from exc
        except BaseException:
            _kill_group(process)
            raise
        finally:
            stdout.close()
        if code != 0:
            stderr.seek(0)
            logger.warning("PDF renderer failed: %s", stderr.read()[-500:].decode(errors="replace"))
            raise AttachmentUnavailable(f"PDF renderer failed (exit {code})")
    return b"".join(chunks)


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


def vendor_confirmation_attachment(ref: str, recipient: str) -> mail.Attachment:
    """The email attachment for the vendor order with public id `ref`, for an email to
    `recipient` (the outbox builds it when the email is sent). The document is the buyer's, so it
    is built only when `recipient` is the order's own address: a forged reference in an outbox
    context never sends one buyer's document to someone else. Raises `AttachmentUnavailable`
    when it cannot or may not be produced, or is over the time or size cap; the email then goes
    out without it."""
    sub = (
        VendorOrder.objects.select_related("order", "vendor").filter(public_id=ref).first()
        if _is_uuid(ref)
        else None
    )
    if sub is None:
        raise ConfirmationUnavailable("vendor order not found")
    if mail.normalise_address(sub.order.email) != mail.normalise_address(recipient):
        raise AttachmentUnavailable("attachment does not belong to the recipient")
    pdf = render_vendor_confirmation_pdf(sub)
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
