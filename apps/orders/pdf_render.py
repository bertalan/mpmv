"""The WeasyPrint side of the confirmation PDF (ORD.9 3/3, ADR-0010).

This module imports no Django: it runs in a child process started by `documents.render_html`
(`python -m apps.orders.pdf_render`), which reads `{"html": ..., "roots": [...]}` as JSON on
stdin and writes the PDF on stdout. A hard timeout in the parent kills it, so a pathological
layout cannot hang the worker, and the child gets no application secrets.

`StaticOnlyFetcher` is the only way the renderer is called and `render_pdf` the only place
WeasyPrint is used: `data:` URIs and files under the given roots load, nothing else. There is no
network access and no redirect to follow. `base_url` is a fixed internal value.
"""

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

#: Not a real address: relative references in the document resolve to nothing.
BASE_URL = "mpmv-document:///"


class ForbiddenResource(ValueError):
    """The document asked for a resource that only static files and `data:` URIs may provide."""


class StaticOnlyFetcher:
    """A WeasyPrint URL fetcher that allows `data:` URIs and files under `roots` only.

    `file:` paths are resolved with `realpath` before the check, so `..`, encoded dots and
    symlinks cannot leave the roots. Every other scheme, and `file:` with a remote host, raises
    `ForbiddenResource` before anything is opened. Redirects are never followed: no HTTP request
    is ever made. It wraps WeasyPrint's own fetcher, which only ever sees `data:` and checked
    `file:` URLs.
    """

    _fail_on_errors = False  # read by WeasyPrint: a refused resource is logged, not fatal

    def __init__(self, roots: Iterable[Path | str]) -> None:
        self._roots = tuple(Path(root).resolve() for root in roots)
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


def render_pdf(html: str, roots: Iterable[Path | str]) -> bytes:
    """Render `html` to a tagged PDF (PDF/UA-1 variant) through the locked-down fetcher. The
    document language and title come from the HTML."""
    from weasyprint import HTML

    document = HTML(string=html, base_url=BASE_URL, url_fetcher=StaticOnlyFetcher(roots))
    return bytes(document.write_pdf(pdf_variant="pdf/ua-1", pdf_tags=True))


def main() -> None:
    request = json.loads(sys.stdin.buffer.read())
    sys.stdout.buffer.write(render_pdf(request["html"], request["roots"]))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
