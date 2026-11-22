"""I18N.20 part 3c: the compliance emails render without English source text (it, de)."""

from __future__ import annotations

import pytest

from apps.core.services import render_email

_LINE = {"quantity": 2, "title": "Brake pad", "variant": "front"}
_CONTEXT = {
    "vendor_name": "Shop",
    "customer_name": "Alex",
    "order_number": "MP-1",
    "submitted_at": "2026-12-01 10:00",
    "reference": "REF-1",
    "statements": [{"vendor_name": "Shop", "reference": "REF-1", "lines": [_LINE]}],
    "lines": [_LINE],
    "url": "https://example.test/p/x",
    "outcome": "action_taken",
    "reason": "Reason text",
    "restriction": "suspend_account",
    "scope": "All listings",
    "valid_until": "2027-01-01",
    "facts": "Facts text",
    "ground_kind": "illegal_content",
    "ground": "Ground text",
    "based_on_notice": True,
    "notice_reference": "N-1",
    "automated_detection": True,
    "automated_decision": False,
    "redress_text": "Redress text",
    "issued_at": "2026-12-01",
    "effective_at": "2027-03-01",
    "exception": "repeated_infringement",
    "count": 2,
    "products": [{"sku": "SKU-1", "vendor_name": "Shop", "missing": "manual"}],
    "more": 3,
    "kind": "complaint",
    "case_id": "7",
    "authority": "Authority name",
    "product_sku": "SKU-1",
    "received_on": "2026-11-01",
    "due_on": "2026-11-15",
}

_CASES = {
    "compliance.withdrawal_acknowledgement": (
        "This confirms that on",
        "The seller will tell you how to return the goods",
        "we received your withdrawal",
        "Hello",
    ),
    "compliance.withdrawal_vendor_notice": (
        "the buyer of order",
        "The refund and return follow",
        "Withdrawal from order",
    ),
    "compliance.notice_received": ("We received your report", "Reported location"),
    "compliance.notice_decision": (
        "We reviewed your report",
        "Our decision on your report",
        "Action taken",
        "Decision:",
    ),
    "compliance.statement_of_reasons": (
        "We restricted your content",
        "Statement of reasons for",
        "Your storefront is suspended",
        "The content is illegal",
        "Internal complaint",
        "Out-of-court dispute settlement",
        "Automated means used",
        "A court",
    ),
    "compliance.termination_notice": (
        "We will end the service",
        "Notice of termination of your seller account",
        "repeated infringements",
        "Until the service ends",
        "How to challenge this decision",
    ),
    "compliance.termination_withdrawn": (
        "We withdrew the notice of termination",
        "is withdrawn",
    ),
    "compliance.safety_case_overdue": (
        "past its deadline",
        "Overdue GPSR safety case",
        "Product SKU",
        "Received on",
        "Due on",
    ),
    "compliance.gpsr_published_incomplete": (
        "lack mandatory GPSR data",
        "And 3 more",
        "fail the GPSR check",
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
