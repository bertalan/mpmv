"""Every template that posts a form carries a CSRF token, except the cookie consent forms (OPS.9).

The STAGE scan ignores ZAP plugin 10202 (Absence of Anti-CSRF Tokens) because the consent banner
and the cookie settings page post to a CSRF-exempt view by design (docs/dev/security-scans.md,
owner 2026-11-17). ZAP cannot scope that ignore to a URL or a form, so this sweep is the guard: a
new POST form without `{% csrf_token %}` anywhere else fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOTS = ("apps", "themes", "templates")
POST_FORM = re.compile(r"<form\b[^>]*\bmethod\s*=\s*[\"']?post\b", re.IGNORECASE)
FORM_BLOCK = re.compile(r"<form\b.*?</form>", re.IGNORECASE | re.DOTALL)
CSRF_EXEMPT = re.compile(r"\bcsrf_exempt\b")
CSRF_TOKEN = re.compile(r"{%\s*csrf_token\s*%}")

# Posted to the CSRF-exempt `consent_save` view: cached pages cannot hold a per-visitor token.
ACCEPTED = {
    "apps/seo/templates/seo/consent/banner.html",
    "apps/seo/templates/seo/consent/preferences.html",
    # The ride theme's banner override posts to the same exempt view, same justification.
    "themes/ride/templates/seo/consent/banner.html",
}


def post_forms_without_csrf() -> set[str]:
    found = set()
    for root in TEMPLATE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.html"):
            text = path.read_text(encoding="utf-8", errors="replace")
            # Per form, not per file: a page with one protected form and one bare form fails.
            for block in FORM_BLOCK.findall(text):
                if POST_FORM.match(block) and not CSRF_TOKEN.search(block):
                    found.add(path.relative_to(ROOT).as_posix())
    return found


def test_only_the_consent_forms_post_without_a_csrf_token() -> None:
    offenders = post_forms_without_csrf()
    new = sorted(offenders - ACCEPTED)
    assert not new, (
        "POST form without {% csrf_token %} (the STAGE scan ignores ZAP 10202, so this test is "
        f"the only guard): {new}"
    )
    gone = sorted(ACCEPTED - offenders)
    assert not gone, f"accepted consent templates no longer match; update ACCEPTED and docs: {gone}"


# The views that may skip Django's CSRF check (ZAP 10202 is ignored, so a new one goes unseen).
ACCEPTED_EXEMPT_VIEWS = {
    "apps/core/views.py": 1,  # email_webhook: the provider signs the request
    "apps/payments/views.py": 1,  # payment_webhook: Stripe signs the request
    "apps/seo/views.py": 1,  # consent_save: cached pages, same-site check instead
}


def csrf_exempt_uses() -> dict[str, int]:
    uses: dict[str, int] = {}
    for root in ("apps", "mpmv"):
        for path in (ROOT / root).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            count = sum(
                1
                for line in path.read_text(encoding="utf-8").splitlines()
                if CSRF_EXEMPT.search(line) and not line.lstrip().startswith(("from ", "import "))
            )
            if count:
                uses[rel] = count
    return uses


def test_only_the_accepted_views_are_csrf_exempt() -> None:
    assert csrf_exempt_uses() == ACCEPTED_EXEMPT_VIEWS, (
        "a new csrf_exempt view: the STAGE scan ignores ZAP 10202, so list it here with its "
        "reason and in docs/dev/security-scans.md only after review"
    )


def test_the_sweep_detects_a_post_form_without_a_token() -> None:
    assert POST_FORM.search('<form class="x" method="POST" action="/a/">')
    assert POST_FORM.search("<form method=post>")
    assert not POST_FORM.search('<form method="get">')
    assert CSRF_TOKEN.search("{%csrf_token%}")
    page = '<form method="post">{% csrf_token %}</form><form method="post"><button></button></form>'
    bare = [b for b in FORM_BLOCK.findall(page) if POST_FORM.match(b) and not CSRF_TOKEN.search(b)]
    assert len(bare) == 1
