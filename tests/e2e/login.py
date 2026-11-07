"""Login steps shared by e2e tests: password on the admin form, then the second factor (CORE.4)."""

from playwright.sync_api import Page


def admin_login(page: Page, base_url: str, email: str, password: str) -> None:
    """Sign in on the staff admin login form (first factor only)."""
    page.goto(f"{base_url}/django-admin/login/")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()


def verify_with_totp(page: Page, token: str) -> None:
    """Answer the MFA verification page with an authenticator-app code."""
    page.get_by_label("Authentication code").fill(token)
    page.get_by_role("button", name="Verify", exact=True).click()
