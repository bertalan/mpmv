"""CMP.9 (WCAG 2.2 SC 3.3.8, spec 14 §14.6): the template lint of the authentication forms.

Every password and code field in the project's templates carries the `autocomplete` token that
lets password managers and the operating system fill it, no field or script blocks paste, and
the login forms built from Django and Wagtail form classes get the same tokens from their
widgets. Turnstile is rendered in a mode that never asks for a puzzle.
"""

import re
import secrets
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.test import override_settings

from apps.accounts.templatetags.captcha import captcha_widget

ROOT = Path(__file__).resolve().parents[4]
TEMPLATE_FILES = sorted(
    [*ROOT.glob("apps/*/templates/**/*.html"), *ROOT.glob("themes/*/templates/**/*.html")]
)
SCRIPT_FILES = sorted([*ROOT.glob("apps/*/static/**/*.js"), *ROOT.glob("themes/*/static/**/*.js")])
# Names of one-time code fields: TOTP (`token`), recovery codes (`code`), email OTPs (VEN.14).
CODE_FIELD_NAMES = frozenset({"token", "code", "otp"})
PASSWORD_TOKENS = frozenset({"current-password", "new-password"})
CLIPBOARD_HANDLERS = ("onpaste", "oncopy", "oncut", "ondrop")


class _Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            self.inputs.append({name: value or "" for name, value in attrs})


def _inputs(path: Path) -> list[dict[str, str]]:
    parser = _Inputs()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.inputs


def _autocomplete_tokens(attrs: dict[str, str]) -> set[str]:
    return set(attrs.get("autocomplete", "").split())


def test_the_lint_sees_the_authentication_templates() -> None:
    names = {path.name for path in TEMPLATE_FILES}
    assert {"verify.html", "totp.html"} <= names


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_password_and_code_fields_carry_their_autocomplete_token(path: Path) -> None:
    for attrs in _inputs(path):
        if attrs.get("type") == "hidden":
            continue
        tokens = _autocomplete_tokens(attrs)
        if attrs.get("type") == "password":
            assert tokens & PASSWORD_TOKENS, f"{path.name}: password field without token {attrs}"
        elif attrs.get("name") in CODE_FIELD_NAMES:
            assert "one-time-code" in tokens, f"{path.name}: code field without token {attrs}"


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_template_blocks_paste(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    for handler in CLIPBOARD_HANDLERS:
        assert f"{handler}=" not in text, f"{path.name} sets {handler}"


@pytest.mark.parametrize("path", SCRIPT_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_script_listens_for_paste(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"""addEventListener\(\s*["'](paste|copy|cut|drop)["']""", text), path


def test_the_totp_field_asks_for_the_numeric_keyboard() -> None:
    for path in TEMPLATE_FILES:
        for attrs in _inputs(path):
            if attrs.get("name") == "token" and attrs.get("type") != "hidden":
                assert attrs.get("inputmode") == "numeric", path.name


def test_the_staff_admin_login_form_lets_password_managers_fill_it() -> None:
    from apps.accounts.forms import StaffLoginForm

    form = StaffLoginForm()
    assert form.fields["username"].widget.attrs["autocomplete"] == "username"
    password = form.fields["password"].widget.attrs
    assert password["autocomplete"] == "current-password"
    assert "data-password-toggle" in password


def test_the_password_toggle_is_a_module_without_inline_code() -> None:
    script = ROOT / "apps/accounts/static/accounts/password-toggle.js"
    text = script.read_text(encoding="utf-8")
    assert "eval(" not in text
    assert "innerHTML" not in text
    assert "aria-pressed" in text


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_page_with_the_captcha_widget_loads_its_script(path: Path) -> None:
    """Without Cloudflare's script the widget never yields a token and the form can never be
    submitted: the user would be locked out of an authentication step."""
    text = path.read_text(encoding="utf-8")
    if "{% captcha_widget %}" in text:
        assert "{% captcha_script %}" in text, f"{path.name}: captcha widget without its script"


@override_settings(CAPTCHA_ENABLED=True, TURNSTILE_SITE_KEY="site-" + secrets.token_hex(4))
def test_turnstile_never_asks_for_a_puzzle() -> None:
    """Managed or invisible challenge: the widget appears only when Cloudflare needs a click
    on its accessible checkbox, never a puzzle (spec 14 §14.6)."""
    html = str(captcha_widget())
    assert 'data-appearance="interaction-only"' in html
    assert 'data-retry="auto"' in html
