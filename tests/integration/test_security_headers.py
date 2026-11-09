"""SEC.1: HTTP hardening header scan (spec 12, requirement SEC.3).

The scan runs the real Django stack with the LIVE security settings applied on top of the test
settings, so it checks exactly what production sends: CSP without 'unsafe-inline', HSTS,
Secure/HttpOnly/SameSite cookies and the other security headers.
"""

import importlib
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.test import Client, override_settings

from apps.accounts.tests.factories import UserFactory

BASE_DIR = Path(__file__).resolve().parents[2]

# Settings that make up the HTTP security posture; everything else stays as in the test settings.
SECURITY_SETTING_PREFIXES = (
    "SECURE_",
    "SESSION_COOKIE_",
    "CSRF_COOKIE_",
    "X_FRAME_OPTIONS",
    "PERMISSIONS_POLICY",
    "MIDDLEWARE",
    "TEMPLATES",
)

SCANNED_PATHS = ["/healthz", "/django-admin/login/"]

EXPECTED_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}

DENIED_FEATURES = ("camera", "microphone", "geolocation", "payment", "usb")


def security_settings(module: str) -> dict[str, Any]:
    """Security-related settings declared by an environment's settings module."""
    settings_module = importlib.import_module(module)
    return {
        name: getattr(settings_module, name)
        for name in dir(settings_module)
        if name.startswith(SECURITY_SETTING_PREFIXES)
    }


def parse_csp(header: str) -> dict[str, list[str]]:
    """Split a Content-Security-Policy header into {directive: [sources]}."""
    directives: dict[str, list[str]] = {}
    for part in header.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0]] = tokens[1:]
    return directives


@pytest.fixture
def live_client() -> Iterator[Client]:
    """A test client running with the LIVE security settings (middleware built inside)."""
    with override_settings(**security_settings("mpmv.settings.live")):
        yield Client()


@pytest.mark.django_db
@pytest.mark.parametrize("path", SCANNED_PATHS)
def test_live_security_headers_are_present(live_client: Client, path: str) -> None:
    response = live_client.get(path, secure=True)
    assert response.status_code == 200
    assert {name: response.headers.get(name) for name in EXPECTED_HEADERS} == EXPECTED_HEADERS


@pytest.mark.django_db
@pytest.mark.parametrize("path", SCANNED_PATHS)
def test_live_csp_is_strict(live_client: Client, path: str) -> None:
    header = live_client.get(path, secure=True).headers["Content-Security-Policy"]
    policy = parse_csp(header)
    assert "'unsafe-inline'" not in header
    assert "'unsafe-eval'" not in header
    assert policy["default-src"] == ["'self'"]
    assert policy["object-src"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"]
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["form-action"] == ["'self'"]
    assert "upgrade-insecure-requests" in policy
    for directive in ("script-src", "style-src"):
        sources = policy[directive]
        assert "'self'" in sources
        assert not {"*", "http:", "https:", "data:"} & set(sources)


@pytest.mark.django_db
@pytest.mark.parametrize("path", SCANNED_PATHS)
def test_live_permissions_policy_denies_powerful_features(live_client: Client, path: str) -> None:
    header = live_client.get(path, secure=True).headers["Permissions-Policy"]
    features = {item.strip() for item in header.split(",")}
    assert {f"{feature}=()" for feature in DENIED_FEATURES} <= features


@pytest.mark.django_db
def test_admin_login_assets_carry_the_csp_nonce(live_client: Client) -> None:
    response = live_client.get("/django-admin/login/", secure=True)
    script_src = parse_csp(response.headers["Content-Security-Policy"])["script-src"]
    nonces = [source for source in script_src if source.startswith("'nonce-")]
    assert len(nonces) == 1
    nonce = nonces[0].removeprefix("'nonce-").removesuffix("'")
    assert f'nonce="{nonce}"' in response.content.decode()


@pytest.mark.django_db
def test_live_csrf_cookie_is_secure_httponly_and_samesite(live_client: Client) -> None:
    cookie = live_client.get("/django-admin/login/", secure=True).cookies["csrftoken"]
    assert cookie["secure"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"


@pytest.mark.django_db
def test_live_session_cookie_is_secure_httponly_and_samesite(live_client: Client) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    UserFactory.create(email="staff@example.com", password=password, superuser=True)
    response = live_client.post(
        "/django-admin/login/",
        {"username": "staff@example.com", "password": password},
        secure=True,
    )
    assert response.status_code == 302
    cookie = response.cookies["sessionid"]
    assert cookie["secure"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"


@pytest.mark.django_db
def test_live_plain_http_redirects_to_https(live_client: Client) -> None:
    response = live_client.get("/django-admin/login/")
    assert response.status_code == 301
    assert response.headers["Location"].startswith("https://")


@pytest.mark.django_db
def test_live_healthz_answers_plain_http_probes(live_client: Client) -> None:
    """The container runtime probes the app directly over HTTP, behind the TLS proxy."""
    response = live_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_strict_csp_and_permissions_policy_apply_in_every_environment(client: Client) -> None:
    """Dev and test use the same CSP, so violations show up long before LIVE."""
    response = client.get("/django-admin/login/")
    header = response.headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in header
    assert parse_csp(header)["default-src"] == ["'self'"]
    assert "camera=()" in response.headers["Permissions-Policy"]
    cookie = response.cookies["csrftoken"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"


def _deploy_check(module: str, **extra: str) -> subprocess.CompletedProcess[str]:
    # A production-like environment: no DJANGO_* or MPMV_TRUSTED_* variables leaking from the
    # test session (the e2e suite sets DJANGO_ALLOW_ASYNC_UNSAFE, which the deploy check rightly
    # rejects).
    inherited = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("DJANGO_", "MPMV_TRUSTED_", "MPMV_EMAIL_"))
    }
    env = {
        **inherited,
        "DJANGO_SETTINGS_MODULE": module,
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS": "shop.example.com",
        "DATABASE_URL": "postgres://mpmv@127.0.0.1:55432/mpmv",
        "MPMV_BASE_URL": "https://shop.example.com",
        # LIVE refuses the console sender and a localhost address (core.E501, core.E502); the
        # provider adapter comes with ARC.19, so the check sees only its dotted path here.
        "MPMV_EMAIL_SENDER": "apps.example.ProviderEmailSender",
        "MPMV_EMAIL_FROM": "no-reply@shop.example.com",
        **extra,
    }
    return subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("module", ["mpmv.settings.live", "mpmv.settings.stage"])
def test_deploy_check_passes(module: str) -> None:
    # Behind Caddy, as in deploy/server/mpmv.env.example (CORE.5).
    result = _deploy_check(
        module, MPMV_TRUSTED_PROXY_HEADER="X-Real-IP", MPMV_TRUSTED_PROXIES="10.89.250.0/24"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deploy_check_fails_without_the_public_base_url() -> None:
    """CMS.1: Wagtail's admin emails would link to localhost."""
    result = _deploy_check(
        "mpmv.settings.live",
        MPMV_TRUSTED_PROXY_HEADER="X-Real-IP",
        MPMV_TRUSTED_PROXIES="10.89.250.0/24",
        MPMV_BASE_URL="",
    )
    assert result.returncode != 0
    assert "cms.E001" in result.stdout + result.stderr


def test_live_refuses_to_start_without_a_real_email_sender() -> None:
    """Coherence check 7: a forgotten email variable must stop LIVE, not drop every email."""
    result = _deploy_check(
        "mpmv.settings.live",
        MPMV_TRUSTED_PROXY_HEADER="X-Real-IP",
        MPMV_TRUSTED_PROXIES="10.89.250.0/24",
        MPMV_EMAIL_SENDER="",
        MPMV_EMAIL_FROM="",
    )
    assert result.returncode != 0
    assert "core.E501" in result.stdout + result.stderr
    assert "core.E502" in result.stdout + result.stderr


def test_stage_keeps_the_console_sender_until_the_provider_is_wired() -> None:
    """STAGE holds synthetic data only: the console sender is allowed there until ARC.19."""
    result = _deploy_check(
        "mpmv.settings.stage",
        MPMV_TRUSTED_PROXY_HEADER="X-Real-IP",
        MPMV_TRUSTED_PROXIES="10.89.250.0/24",
        MPMV_EMAIL_SENDER="",
        MPMV_EMAIL_FROM="",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deploy_check_warns_without_a_trusted_proxy_header() -> None:
    """CORE.5: behind the proxy every client would share one IP throttle bucket."""
    result = _deploy_check("mpmv.settings.live")
    assert result.returncode != 0
    assert "core.W001" in result.stdout + result.stderr
