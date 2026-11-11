"""Login steps shared by e2e tests: password on the admin form, then the second factor (CORE.4)."""

from playwright.sync_api import Page


def admin_login(page: Page, base_url: str, email: str, password: str) -> None:
    """Sign in on the staff admin login form (first factor only)."""
    page.goto(f"{base_url}/django-admin/login/")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()


def verify_with_totp(page: Page, token: str, *, operational: bool = False) -> None:
    """Answer the MFA verification page with an authenticator-app code.

    `operational=True` then puts the session in operational mode (see `make_operational`).
    """
    page.get_by_label("Authentication code").fill(token)
    page.get_by_role("button", name="Verify", exact=True).click()
    if operational:
        page.wait_for_url(lambda url: "/account/mfa/verify" not in url)
        make_operational(page)


def make_operational(page: Page) -> None:
    """Put the browser's session in operational mode (D-32, CORE.25).

    The real switch needs a passkey; the e2e suite has no authenticator, so the test writes the
    session state (server side, in the shared test database) the way `levels.start_operational`
    does. Tests of the switch itself live in the integration suite.
    """
    import time

    from django.conf import settings
    from django.contrib.sessions.backends.db import SessionStore

    from apps.accounts import levels

    cookies = {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}
    store = SessionStore(session_key=cookies[settings.SESSION_COOKIE_NAME])
    store[levels.STARTED_KEY] = store[levels.ACTIVITY_KEY] = time.time()
    store.save()


def fresh_passkey(page: Page) -> None:
    """Record a fresh passkey with user verification in the browser's session (D-32 group 3).

    Like `make_operational`, it writes the server-side session state because the e2e suite has
    no authenticator; the ceremony itself is tested in the integration suite.
    """
    import time

    from django.conf import settings
    from django.contrib.sessions.backends.db import SessionStore

    from apps.accounts.mfa_session import (
        VERIFIED_AT_SESSION_KEY,
        VERIFIED_KIND_SESSION_KEY,
        VERIFIED_UV_SESSION_KEY,
    )

    cookies = {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}
    store = SessionStore(session_key=cookies[settings.SESSION_COOKIE_NAME])
    store[VERIFIED_AT_SESSION_KEY] = time.time()
    store[VERIFIED_KIND_SESSION_KEY] = "passkey"
    store[VERIFIED_UV_SESSION_KEY] = True
    store.save()
