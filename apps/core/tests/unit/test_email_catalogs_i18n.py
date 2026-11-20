"""I18N.20 part 3c: the catalog and core emails render without English source text (it, de)."""

from __future__ import annotations

import pytest

from apps.core.services import render_email

_BRAND = {
    "vendor_name": "Shop",
    "brands": [
        {"name": "Acmee", "products": [{"title": "Widget", "sku": "W-1"}]},
        {"name": "Zed", "products": []},
    ],
}
_ALERT = {"key": "queue.stuck", "summary": "Queue stuck", "details": {"depth": 12}}

_CASES = {
    "catalog.brand_rejected": (
        _BRAND,
        (
            "Brand not approved",
            "did not approve the brand",
            "No products used it.",
            "Open each product",
        ),
    ),
    "core.ops_alert": (
        _ALERT,
        ("Operations alert", "An operations alert was raised", "Alert:"),
    ),
}


@pytest.mark.parametrize("language", ["it", "de"])
@pytest.mark.parametrize("key", sorted(_CASES))
def test_the_email_has_no_english_source_sentence(key: str, language: str) -> None:
    context, english = _CASES[key]
    rendered = render_email(key, language, context)
    parts = (rendered.subject, rendered.text_body, rendered.html_body)
    for sentence in english:
        for part in parts:
            assert sentence not in part, (key, language, sentence)
    assert f'lang="{language}"' in rendered.html_body
    assert rendered.subject.strip()
