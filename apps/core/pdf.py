"""Render an HTML document to a tagged PDF in a locked-down child process (ADR-0010).

Shared by the order confirmation (`apps.orders.documents`) and the sales terms download
(`apps.vendors`, VEN.36). WeasyPrint runs only in `pdf_render` (no Django), in a child process with
a hard timeout and no application secrets; see there for the locked-down fetcher (`data:` URIs
and static files only, no network, no redirects). WeasyPrint is never imported in the caller.
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

from django.apps import apps as django_apps
from django.conf import settings

from . import pdf_render
from .services import AttachmentUnavailable

logger = logging.getLogger(__name__)

#: The PDF is 1 to 3 pages; anything bigger or slower is a fault.
MAX_PDF_BYTES = 1_048_576
MAX_RENDER_SECONDS = 10.0


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
    return [sys.executable, "-m", "apps.core.pdf_render"]


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
