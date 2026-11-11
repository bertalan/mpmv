"""The hardened WeasyPrint fetcher of the per-vendor confirmation PDF (ORD.9 3/3, ADR-0010).

Only `data:` URIs and files under the static roots may be loaded; there is no network access and
no redirect to follow. Each rule is a test here; the document tests are in the integration suite.
"""

import ast
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from apps.orders import documents

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    root.mkdir()
    (root / "logo.css").write_text("body { color: black }")
    return root


@pytest.fixture
def fetcher(static_root: Path) -> documents.StaticOnlyFetcher:
    return documents.StaticOnlyFetcher(roots=[static_root])


def test_data_uris_are_allowed(fetcher: documents.StaticOnlyFetcher) -> None:
    response = fetcher.fetch("data:text/plain;base64,aGk=")
    assert response.read() == b"hi"


def test_files_under_a_static_root_are_allowed(
    fetcher: documents.StaticOnlyFetcher, static_root: Path
) -> None:
    response = fetcher.fetch((static_root / "logo.css").as_uri())
    assert b"color: black" in response.read()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.css",
        "https://example.com/a.png",
        "HTTPS://example.com/a.png",
        "ftp://example.com/a.png",
        "gopher://example.com/",
        "//example.com/a.png",
        "javascript:alert(1)",
        "relative/path.png",
        "",
    ],
)
def test_every_scheme_but_data_and_static_files_is_refused(
    fetcher: documents.StaticOnlyFetcher, url: str
) -> None:
    with pytest.raises(documents.ForbiddenResource):
        fetcher.fetch(url)


def test_a_file_outside_the_static_roots_is_refused(
    fetcher: documents.StaticOnlyFetcher, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the PDF")
    with pytest.raises(documents.ForbiddenResource):
        fetcher.fetch(secret.as_uri())
    with pytest.raises(documents.ForbiddenResource):
        fetcher.fetch("file:///etc/hostname")


def test_dot_dot_and_encoded_dot_dot_cannot_leave_the_root(
    fetcher: documents.StaticOnlyFetcher, static_root: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret.txt").write_text("not for the PDF")
    for tail in ("../secret.txt", "%2e%2e/secret.txt", "..%2fsecret.txt"):
        with pytest.raises(documents.ForbiddenResource):
            fetcher.fetch(f"file://{static_root.as_posix()}/{tail}")


def test_a_symlink_cannot_leave_the_root(
    fetcher: documents.StaticOnlyFetcher, static_root: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the PDF")
    (static_root / "link.txt").symlink_to(secret)
    with pytest.raises(documents.ForbiddenResource):
        fetcher.fetch((static_root / "link.txt").as_uri())


def test_a_file_url_with_a_remote_host_is_refused(
    fetcher: documents.StaticOnlyFetcher, static_root: Path
) -> None:
    with pytest.raises(documents.ForbiddenResource):
        fetcher.fetch(f"file://evil.example{static_root.as_posix()}/logo.css")


def test_the_default_roots_are_the_static_directories() -> None:
    roots = documents.static_roots()
    assert roots
    assert all(root.is_absolute() for root in roots)
    assert Path("/") not in roots
    assert not any(root == ROOT or root.name == "media" for root in roots)


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        self.send_response(302)
        self.send_header("Location", "/second")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def local_server() -> Iterator[tuple[str, list[str]]]:
    _Recorder.hits = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _Recorder.hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_redirecting_http_url_is_refused_without_any_request(
    fetcher: documents.StaticOnlyFetcher, local_server: tuple[str, list[str]]
) -> None:
    base, hits = local_server
    with pytest.raises(documents.ForbiddenResource):
        fetcher.fetch(f"{base}/first")
    assert hits == []  # nothing was sent, so there was nothing to redirect


def test_the_fetcher_follows_no_redirect_handler() -> None:
    from urllib.request import HTTPRedirectHandler

    fetcher = documents.StaticOnlyFetcher(roots=[])
    assert not any(isinstance(handler, HTTPRedirectHandler) for handler in fetcher.handlers)


def test_a_document_with_remote_resources_fetches_none_of_them(
    local_server: tuple[str, list[str]],
) -> None:
    """End to end through WeasyPrint: a stylesheet, an image and a CSS url() all aimed at a
    local server are refused, so the server sees no request and the PDF is still produced."""
    base, hits = local_server
    html = (
        f'<html><head><link rel="stylesheet" href="{base}/a.css">'
        f'<style>@import url("{base}/b.css"); p {{ background: url({base}/c.png) }}</style>'
        f'</head><body><p>text</p><img src="{base}/d.png"></body></html>'
    )
    pdf = documents.render_html(html, lang="en")
    assert pdf.startswith(b"%PDF")
    assert hits == []


def _python_files() -> list[Path]:
    skipped = {".venv", ".claude", "node_modules", "build", "private", "staticfiles"}
    return [
        path
        for path in (ROOT / "apps").rglob("*.py")
        if not skipped & set(path.parts) and "tests" not in path.parts
    ]


def test_weasyprint_is_only_called_through_the_fetcher() -> None:
    """The fetcher is the only way the renderer is called: no code outside `documents.py`
    imports WeasyPrint, and its one `HTML(...)` call passes `url_fetcher=`."""
    importers = []
    for path in _python_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(name == "weasyprint" or name.startswith("weasyprint.") for name in names):
                importers.append(path.relative_to(ROOT).as_posix())
    assert set(importers) == {"apps/orders/documents.py"}

    tree = ast.parse((ROOT / "apps/orders/documents.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"HTML", "CSS"}
    ]
    assert len(calls) == 1
    assert calls[0].func.id == "HTML"  # type: ignore[attr-defined]
    assert {kw.arg for kw in calls[0].keywords} >= {"url_fetcher", "base_url"}
