"""I18N.20 part 3c: the vendor emails render without English source text (it, de)."""

from __future__ import annotations

import pytest

from apps.core.services import render_email

_CONTEXT = {
    "vendor_name": "Shop",
    "language": "it",
    "fields": "tagline",
    "reason": "Wrong text",
    "action_url": "https://example.test/claim/x",
    "action_link_expires_at": "2026-12-01 10:00",
}

_CASES = {
    "vendors.claim_link": (
        "has been set up on the platform",
        "Claim the vendor",
        "The platform staff review your claim",
        "on the platform",
    ),
    "vendors.profile_changed": (
        "changed its storefront profile",
        "The change is already live",
    ),
    "vendors.profile_reverted": (
        "Platform staff reverted your change",
        "was reverted",
    ),
}


@pytest.mark.parametrize("language", ["it", "de"])
@pytest.mark.parametrize("key", sorted(_CASES))
def test_the_email_has_no_english_source_sentence(key: str, language: str) -> None:
    rendered = render_email(key, language, _CONTEXT)
    parts = (rendered.subject, rendered.text_body, rendered.html_body)
    for sentence in _CASES[key]:
        for part in parts:
            assert sentence not in part, (key, language, sentence)
    assert f'lang="{language}"' in rendered.html_body
    assert rendered.subject.strip()
