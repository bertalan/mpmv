"""The hardened WeasyPrint fetcher of the per-vendor confirmation PDF (ORD.9 3/3, ADR-0010).

Only `data:` URIs and files under the static roots may be loaded; there is no network access and
no redirect to follow. Each rule is a test here; the document tests are in the integration suite.
"""

import ast
import http.server
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from apps.orders import documents, pdf_render

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    root.mkdir()
    (root / "logo.css").write_text("body { color: black }")
    return root


@pytest.fixture
def fetcher(static_root: Path) -> pdf_render.StaticOnlyFetcher:
    return pdf_render.StaticOnlyFetcher(roots=[static_root])


def test_data_uris_are_allowed(fetcher: pdf_render.StaticOnlyFetcher) -> None:
    response = fetcher.fetch("data:text/plain;base64,aGk=")
    assert response.read() == b"hi"


def test_files_under_a_static_root_are_allowed(
    fetcher: pdf_render.StaticOnlyFetcher, static_root: Path
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
    fetcher: pdf_render.StaticOnlyFetcher, url: str
) -> None:
    with pytest.raises(pdf_render.ForbiddenResource):
        fetcher.fetch(url)


def test_a_file_outside_the_static_roots_is_refused(
    fetcher: pdf_render.StaticOnlyFetcher, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the PDF")
    with pytest.raises(pdf_render.ForbiddenResource):
        fetcher.fetch(secret.as_uri())
    with pytest.raises(pdf_render.ForbiddenResource):
        fetcher.fetch("file:///etc/hostname")


def test_dot_dot_and_encoded_dot_dot_cannot_leave_the_root(
    fetcher: pdf_render.StaticOnlyFetcher, static_root: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret.txt").write_text("not for the PDF")
    for tail in ("../secret.txt", "%2e%2e/secret.txt", "..%2fsecret.txt"):
        with pytest.raises(pdf_render.ForbiddenResource):
            fetcher.fetch(f"file://{static_root.as_posix()}/{tail}")


def test_a_symlink_cannot_leave_the_root(
    fetcher: pdf_render.StaticOnlyFetcher, static_root: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the PDF")
    (static_root / "link.txt").symlink_to(secret)
    with pytest.raises(pdf_render.ForbiddenResource):
        fetcher.fetch((static_root / "link.txt").as_uri())


def test_a_file_url_with_a_remote_host_is_refused(
    fetcher: pdf_render.StaticOnlyFetcher, static_root: Path
) -> None:
    with pytest.raises(pdf_render.ForbiddenResource):
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
    fetcher: pdf_render.StaticOnlyFetcher, local_server: tuple[str, list[str]]
) -> None:
    base, hits = local_server
    with pytest.raises(pdf_render.ForbiddenResource):
        fetcher.fetch(f"{base}/first")
    assert hits == []  # nothing was sent, so there was nothing to redirect


def test_the_fetcher_follows_no_redirect_handler() -> None:
    from urllib.request import HTTPRedirectHandler

    fetcher = pdf_render.StaticOnlyFetcher(roots=[])
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
    pdf = pdf_render.render_pdf(html, [])
    assert pdf.startswith(b"%PDF")
    assert hits == []


def _python_files() -> list[Path]:
    skipped = {".venv", ".claude", "node_modules", "build", "private", "staticfiles"}
    return [
        path
        for path in (ROOT / "apps").rglob("*.py")
        if not skipped & set(path.relative_to(ROOT).parts)
        and "tests" not in path.relative_to(ROOT).parts
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
    assert set(importers) == {"apps/orders/pdf_render.py"}

    tree = ast.parse((ROOT / "apps/orders/pdf_render.py").read_text())
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


# --- The renderer runs in its own process with a hard timeout ------------------------------


def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_renderer_that_hangs_is_killed_at_the_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from apps.core.services import AttachmentUnavailable

    pid_file = tmp_path / "pid"
    code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
    monkeypatch.setattr(documents, "_renderer_command", lambda: _child(code))
    monkeypatch.setattr(documents, "MAX_RENDER_SECONDS", 1.5)
    started = time.monotonic()
    with pytest.raises(AttachmentUnavailable, match="timed out"):
        documents.render_html("<p>x</p>")
    assert time.monotonic() - started < 20
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the child is gone, not left running


def test_a_renderer_that_fails_is_an_unavailable_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.core.services import AttachmentUnavailable

    monkeypatch.setattr(documents, "_renderer_command", lambda: _child("import sys; sys.exit(3)"))
    with pytest.raises(AttachmentUnavailable, match="failed"):
        documents.render_html("<p>x</p>")


def test_output_over_the_size_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.core.services import AttachmentUnavailable

    monkeypatch.setattr(
        documents,
        "_renderer_command",
        lambda: _child("import sys; sys.stdout.buffer.write(b'%PDF' + b'x' * 5000)"),
    )
    monkeypatch.setattr(documents, "MAX_PDF_BYTES", 1000)
    with pytest.raises(AttachmentUnavailable, match="cap"):
        documents.render_html("<p>x</p>")


def test_the_renderer_child_gets_no_application_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DJANGO_SECRET_KEY", "not-for-the-child")
    monkeypatch.setenv("DATABASE_URL", "postgres://not-for-the-child")
    env = documents._renderer_env()
    assert "DJANGO_SECRET_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "PATH" in env


def test_output_over_the_cap_stops_the_read_early_and_kills_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from apps.core.services import AttachmentUnavailable

    pid_file = tmp_path / "pid"
    code = (
        f"import os, sys, time; open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "sys.stdout.buffer.write(b'x' * 5000); sys.stdout.flush(); time.sleep(120)"
    )
    monkeypatch.setattr(documents, "_renderer_command", lambda: _child(code))
    monkeypatch.setattr(documents, "MAX_PDF_BYTES", 1000)
    started = time.monotonic()
    with pytest.raises(AttachmentUnavailable, match="cap"):
        documents.render_html("<p>x</p>")
    assert time.monotonic() - started < 20  # did not wait for the child to finish
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_the_whole_process_group_is_killed_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from apps.core.services import AttachmentUnavailable

    pid_file = tmp_path / "grandchild"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(120)"
    )
    monkeypatch.setattr(documents, "_renderer_command", lambda: _child(code))
    monkeypatch.setattr(documents, "MAX_RENDER_SECONDS", 1.5)
    with pytest.raises(AttachmentUnavailable, match="timed out"):
        documents.render_html("<p>x</p>")
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def _run_limited(body: str) -> int:
    code = (
        "from apps.orders import pdf_render\n"
        "pdf_render.apply_limits(memory_bytes=100_000_000, cpu_seconds=1)\n" + body
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, timeout=60, check=False
    ).returncode


def test_the_parent_process_never_imports_weasyprint() -> None:
    """Only the child imports WeasyPrint; the upload guard's security argument relies on it."""
    code = (
        "import sys, django\n"
        "django.setup()\n"
        "import apps.orders.documents\n"
        "assert 'weasyprint' not in sys.modules\n"
    )
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "mpmv.settings.test"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_a_memory_hog_hits_the_address_space_limit() -> None:
    assert _run_limited("x = bytearray(400_000_000)") != 0
    assert _run_limited("x = bytearray(1_000_000)") == 0


def test_a_cpu_hog_is_stopped_by_the_cpu_limit() -> None:
    started = time.monotonic()
    assert _run_limited("while True: pass") != 0
    assert time.monotonic() - started < 30


def test_the_documented_limits_are_sane() -> None:
    assert pdf_render.MEMORY_LIMIT_BYTES == 1 << 30
    assert documents.cpu_limit_seconds() > documents.MAX_RENDER_SECONDS
