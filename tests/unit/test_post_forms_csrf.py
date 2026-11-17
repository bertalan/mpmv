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
CSRF_TOKEN = re.compile(r"{%\s*csrf_token\s*%}")

# Posted to the CSRF-exempt `consent_save` view: cached pages cannot hold a per-visitor token.
ACCEPTED = {
    "apps/seo/templates/seo/consent/banner.html",
    "apps/seo/templates/seo/consent/preferences.html",
}


def post_forms_without_csrf() -> set[str]:
    found = set()
    for root in TEMPLATE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.html"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if POST_FORM.search(text) and not CSRF_TOKEN.search(text):
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


def test_the_sweep_detects_a_post_form_without_a_token() -> None:
    assert POST_FORM.search('<form class="x" method="POST" action="/a/">')
    assert POST_FORM.search("<form method=post>")
    assert not POST_FORM.search('<form method="get">')
    assert CSRF_TOKEN.search("{%csrf_token%}")
