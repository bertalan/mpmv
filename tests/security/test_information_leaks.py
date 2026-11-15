"""SEC.5 attack scenarios, item 10: information leaks (owner security wave, 2026-11-09).

The attacker probes for debug output, stack traces, version banners, files that should never be
public (`.git`, `.env`, source maps, backups) and private pages kept by a shared cache. Each case
asserts that nothing leaks with the LIVE settings applied.

Already covered elsewhere (not repeated here):
- API v1 500/409/404 problems carry no exception text, SQL or internal ids
  (apps/api/tests/integration/test_v1_errors.py); validation problems keep only location,
  message and type (apps/api/tests/unit/test_problems.py);
- the 400 and 500 pages render without a database query (tests/integration/test_error_pages.py);
- the image build context excludes `.git`, `.env` and local state
  (tests/unit/test_containerfile.py); Caddy serves only image files under /media/
  (tests/unit/test_quadlets.py);
- secure, HttpOnly cookies and the security headers on LIVE
  (tests/integration/test_security_headers.py); log redaction (tests/unit/test_log_redaction.py).

Tests in tests/security/ carry their suite as a marker (`unit` or `integration`, conftest.py).
"""

import ast
import fnmatch
import importlib
import re
import runpy
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import django
import pytest
import wagtail
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.apps import StaticFilesConfig
from django.test import Client, override_settings

from tests.security.leak_urls import INTERNAL_DETAIL

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ("mpmv.settings.live", "mpmv.settings.stage")

# --- DEBUG and dev-only tooling


@pytest.mark.unit
@pytest.mark.parametrize("module", ENVIRONMENTS)
def test_debug_is_off_on_stage_and_live_whatever_the_environment_says(
    module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator (or an attacker with access to the env file) cannot switch debug pages on.
    for name in ("DEBUG", "DJANGO_DEBUG", "MPMV_DEBUG"):
        monkeypatch.setenv(name, "1")
    declared = runpy.run_module(module)
    assert declared["DEBUG"] is False
    assert declared.get("DEBUG_PROPAGATE_EXCEPTIONS", False) is False
    for engine in declared["TEMPLATES"]:
        assert engine.get("OPTIONS", {}).get("debug", False) is False
    tooling = {"debug_toolbar", "django_extensions", "silk", "django_browser_reload"}
    assert not tooling & set(declared["INSTALLED_APPS"])


@pytest.mark.unit
def test_the_app_server_banner_has_no_version() -> None:
    from gunicorn import SERVER

    # gunicorn sends `Server: <SERVER>`; a version number would tell an attacker what to try.
    assert not re.search(r"\d", SERVER)


# --- Version headers and stack traces


SECURITY_SETTING_PREFIXES = ("SECURE_", "SESSION_COOKIE_", "CSRF_COOKIE_", "MIDDLEWARE")
VERSION_HEADERS = ("Server", "X-Powered-By", "X-Runtime", "X-Version", "X-Generator", "Via")


def _live_overrides() -> dict[str, Any]:
    live = importlib.import_module("mpmv.settings.live")
    return {
        name: getattr(live, name)
        for name in dir(live)
        if name.startswith(SECURITY_SETTING_PREFIXES)
    } | {"DEBUG": False}


@pytest.fixture
def live_client() -> Iterator[Client]:
    """The test client with LIVE's security settings and middleware (WhiteNoise included)."""
    with override_settings(**_live_overrides()):
        yield Client(raise_request_exception=False)


def _versions() -> list[str]:
    return [django.get_version(), wagtail.__version__]


def _assert_no_banner(response: Any) -> None:
    for header in VERSION_HEADERS:
        assert header not in response.headers, header
    for name, value in response.headers.items():
        for version in _versions():
            assert version not in value, f"{name}: {value}"


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize(
    "path", ["/", "/no-such-page-7f3a/", "/api/v1/no-such-endpoint", "/django-admin/login/"]
)
def test_no_version_banner_in_response_headers(live_client: Client, path: str) -> None:
    _assert_no_banner(live_client.get(path, secure=True))


def _assert_no_internals(body: str) -> None:
    for fragment in (*INTERNAL_DETAIL.split(), "Traceback", "RuntimeError", 'File "'):
        assert fragment not in body, fragment
    for marker in ("no_such_table", "psycopg", "django.db", "settings.py", "DJANGO_"):
        assert marker not in body, marker
    for version in _versions():
        assert version not in body


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.urls("tests.security.leak_urls")
@pytest.mark.parametrize("path", ["/leak-test/boom/", "/leak-test/database/"])
def test_a_server_error_page_shows_no_stack_trace_or_internals(
    live_client: Client, path: str
) -> None:
    response = live_client.get(path, secure=True)
    assert response.status_code == 500
    _assert_no_internals(response.content.decode())
    _assert_no_banner(response)


@pytest.mark.integration
@pytest.mark.django_db
def test_a_404_does_not_list_the_url_configuration(live_client: Client) -> None:
    response = live_client.get("/no-such-page-7f3a/", secure=True)
    assert response.status_code == 404
    body = response.content.decode()
    for marker in ("Using the URLconf", "urlpatterns", "mpmv.urls", "django-admin/"):
        assert marker not in body, marker


@pytest.mark.integration
@pytest.mark.django_db
def test_an_unsupported_api_method_answers_without_internals(live_client: Client) -> None:
    response = live_client.generic("PATCH", "/api/v1/catalog/attributes", secure=True)
    assert response.status_code == 405
    _assert_no_internals(response.content.decode())
    _assert_no_banner(response)


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    reason="Finding: django-ninja answers an unsupported method with its own text/html 405 "
    "('Method not allowed'), outside the problem handlers; no internals, but not RFC 9457",
)
def test_an_unsupported_api_method_is_a_problem(live_client: Client) -> None:
    response = live_client.generic("PATCH", "/api/v1/catalog/attributes", secure=True)
    assert response["Content-Type"] == "application/problem+json"


# --- Files that must never be served

NEVER_SERVED = [
    "/.git/config",
    "/.git/HEAD",
    "/.env",
    "/.env.secrets",
    "/it/.env",
    "/.dockerignore",
    "/manage.py",
    "/pyproject.toml",
    "/uv.lock",
    "/mpmv/settings/base.py",
    "/settings.py.bak",
    "/urls.py.orig",
    "/index.html~",
    "/backup.sql",
    "/dump.sql.gz",
    "/static/.env",
    "/static/.gitignore",
    "/static/backup.sql",
    "/static/apps/themes/engine.py",
    "/static/themes/base/theme.css.map",
    "/static/admin/js/core.js~",
    "/media/.env",
    "/media/backup.sql",
    "/media/original_images/x.png.bak",
]


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("path", NEVER_SERVED)
def test_repository_secret_and_backup_files_are_never_served(
    live_client: Client, path: str
) -> None:
    response = live_client.get(path, secure=True)
    assert response.status_code == 404
    body = response.content.decode(errors="replace")
    for marker in ("[core]", "repositoryformatversion", "SECRET_KEY", "DATABASE_URL", "ref: "):
        assert marker not in body, marker


# Collectstatic leaves these out of STATIC_ROOT (the files WhiteNoise serves on LIVE).
_COLLECT_IGNORED = StaticFilesConfig.ignore_patterns
_NEVER_STATIC = re.compile(
    r"(\.bak|~|\.orig|\.rej|\.sql|\.sql\.gz|\.dump|\.swp|\.env|\.py|\.pyc|\.log|\.old|\.tmp)$"
    r"|(^|/)\.[^/]+$"
)
# Source maps of vendored third-party libraries: their sources are public already. Any other
# source map (our theme build, our scripts) would publish our own sources.
THIRD_PARTY_SOURCE_MAPS = {
    "ninja/swagger-ui.css.map",
    "ninja/swagger-ui-bundle.js.map",
    "ninja/redoc.standalone.js.map",
    "vendor/leaflet/leaflet.js.map",
}


def _collected_static() -> list[str]:
    def ignored(path: str) -> bool:
        parts = path.split("/")
        return any(
            fnmatch.fnmatchcase(part, pattern) for part in parts for pattern in _COLLECT_IGNORED
        )

    return sorted(
        path for finder in finders.get_finders() for path, _ in finder.list([]) if not ignored(path)
    )


@pytest.mark.unit
def test_collected_static_files_hold_no_backups_dotfiles_or_sources() -> None:
    collected = _collected_static()
    assert collected
    assert [path for path in collected if _NEVER_STATIC.search(path)] == []


@pytest.mark.unit
def test_the_only_source_maps_are_third_party_libraries() -> None:
    maps = {path for path in _collected_static() if path.endswith(".map")}
    assert maps <= THIRD_PARTY_SOURCE_MAPS


# --- Private data and caches


def _cache_page_uses() -> list[str]:
    uses: list[str] = []
    for path in sorted((ROOT / "apps").rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(node, "id", None) or getattr(node, "attr", None)
            if name in {"cache_page", "CacheMiddleware", "cache_control"}:
                uses.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}")
    return uses


@pytest.mark.unit
@pytest.mark.parametrize("module", [*ENVIRONMENTS, "mpmv.settings.base"])
def test_no_full_page_cache_could_serve_one_users_page_to_another(module: str) -> None:
    middleware = runpy.run_module(module)["MIDDLEWARE"]
    page_caches = {
        "django.middleware.cache.UpdateCacheMiddleware",
        "django.middleware.cache.FetchFromCacheMiddleware",
        "django.middleware.cache.CacheMiddleware",
    }
    assert not page_caches & set(middleware)
    # A view-level page cache needs a review of what it may store: none exists today.
    assert _cache_page_uses() == []


@pytest.mark.unit
def test_the_proxy_marks_only_public_images_as_cacheable() -> None:
    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text()
    # No response cache in the proxy, and `public` caching only inside the public media block.
    assert not re.search(r"^\s*cache\b", caddyfile, re.MULTILINE)
    media_block = caddyfile.split("handle @public_media {", 1)[1].split("\n\t}", 1)[0]
    assert caddyfile.count("Cache-Control") == media_block.count("Cache-Control") == 1


@pytest.fixture
def signed_in(db: None, mfa_login: Callable[[Client, Any], Client]) -> Client:
    from apps.accounts.tests.factories import UserFactory

    client = Client()
    mfa_login(client, UserFactory.create())
    return client


MFA_PAGES = ["/account/mfa/", "/account/mfa/setup/", "/account/mfa/totp/"]


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", *MFA_PAGES])
def test_signed_in_pages_are_never_shared_cache_entries(signed_in: Client, path: str) -> None:
    response = signed_in.get(path)
    cache_control = response.get("Cache-Control", "")
    assert "public" not in cache_control
    vary = {part.strip().lower() for part in response.get("Vary", "").split(",")}
    assert "cookie" in vary or "private" in cache_control or "no-store" in cache_control


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("path", [*MFA_PAGES, "/account/mfa/recovery-codes/"])
def test_mfa_pages_with_secrets_are_never_stored(signed_in: Client, path: str) -> None:
    response = signed_in.get(path)
    assert response.status_code == 200
    cache_control = response.get("Cache-Control", "")
    assert "no-store" in cache_control
    assert "private" in cache_control


@pytest.mark.integration
@pytest.mark.django_db
def test_an_anonymous_page_sets_no_session_a_cache_could_replay(client: Client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "sessionid" not in response.cookies
