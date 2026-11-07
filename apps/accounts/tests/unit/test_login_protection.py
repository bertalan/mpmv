"""CORE.5: throttle keys, lockout policies, settings and configuration checks (no database)."""

import runpy
import secrets

import pytest
from django.test import override_settings

from apps.accounts.checks import check_captcha_settings
from apps.accounts.services import (
    ACCOUNT_LOCKOUT,
    IP_LOCKOUT,
    login_throttle_keys,
)


def test_login_keys_normalise_the_email_and_bucket_the_ip() -> None:
    keys = login_throttle_keys("  Someone@Example.COM ", "2001:db8:1:2::99")
    assert keys == {
        "login:account:someone@example.com": ACCOUNT_LOCKOUT,
        "login:ip:2001:db8:1:2::/64": IP_LOCKOUT,
    }


def test_login_keys_skip_what_is_unknown() -> None:
    assert login_throttle_keys(None, None) == {}
    assert login_throttle_keys("", "203.0.113.7") == {"login:ip:203.0.113.7": IP_LOCKOUT}


def test_overlong_identifiers_are_truncated_to_the_email_limit() -> None:
    [key] = login_throttle_keys("x" * 1000, None)
    assert key == "login:account:" + "x" * 254


def test_an_ip_tolerates_more_failures_than_one_account() -> None:
    """Many people can share one address (offices, mobile carriers)."""
    assert IP_LOCKOUT.free_attempts > ACCOUNT_LOCKOUT.free_attempts


@override_settings(CAPTCHA_ENABLED=True, TURNSTILE_SITE_KEY="", TURNSTILE_SECRET_KEY="")
def test_enabled_captcha_without_keys_fails_the_system_check() -> None:
    errors = check_captcha_settings(None)
    assert [e.id for e in errors] == ["accounts.E001"]
    assert "TURNSTILE_SITE_KEY" in errors[0].msg
    assert "TURNSTILE_SECRET_KEY" in errors[0].msg


@override_settings(
    CAPTCHA_ENABLED=True,
    TURNSTILE_SITE_KEY=secrets.token_urlsafe(8),
    TURNSTILE_SECRET_KEY=secrets.token_urlsafe(8),
)
def test_enabled_captcha_with_keys_passes_the_system_check() -> None:
    assert check_captcha_settings(None) == []


@override_settings(CAPTCHA_ENABLED=False, TURNSTILE_SITE_KEY="", TURNSTILE_SECRET_KEY="")
def test_disabled_captcha_needs_no_keys() -> None:
    assert check_captcha_settings(None) == []


@pytest.mark.parametrize(("flag", "enabled"), [("", False), ("false", False), ("true", True)])
def test_captcha_is_enabled_only_explicitly_and_then_allowed_by_csp(
    monkeypatch: pytest.MonkeyPatch, flag: str, enabled: bool
) -> None:
    monkeypatch.setenv("MPMV_CAPTCHA_ENABLED", flag)
    settings = runpy.run_module("mpmv.settings.base")  # re-read: the cached module is not
    assert settings["CAPTCHA_ENABLED"] is enabled
    csp = settings["SECURE_CSP"]
    origin = "https://challenges.cloudflare.com"
    assert (origin in csp["script-src"]) is enabled
    assert (origin in csp.get("frame-src", [])) is enabled


def test_the_lockout_backend_runs_before_any_password_check() -> None:
    settings = runpy.run_module("mpmv.settings.base")
    backends = settings["AUTHENTICATION_BACKENDS"]
    assert backends[0] == "apps.accounts.backends.LoginProtectionBackend"
    assert "django.contrib.auth.backends.ModelBackend" in backends
