"""Keyed pseudonyms and e-mail normalisation for counters."""

import hashlib
import secrets

from django.test import override_settings

from apps.core.privacy import keyed_hash, normalise_email


def test_the_digest_is_stable_and_separated_by_purpose() -> None:
    assert keyed_hash("a", purpose="x") == keyed_hash("a", purpose="x")
    assert keyed_hash("a", purpose="x") != keyed_hash("a", purpose="y")
    assert keyed_hash("a", purpose="x") != keyed_hash("b", purpose="x")


def test_the_digest_is_keyed_not_a_plain_sha256() -> None:

    plain = hashlib.sha256(b"x\0a").hexdigest()[:32]
    assert keyed_hash("a", purpose="x") != plain
    before = keyed_hash("a", purpose="x")
    with override_settings(PRIVACY_PEPPER=secrets.token_urlsafe(16)):
        assert keyed_hash("a", purpose="x") != before


def test_email_variants_of_one_mailbox_normalise_alike() -> None:
    assert normalise_email("Ada+news@Example.ORG ") == "ada@example.org"
    assert normalise_email("ada@example.org") == "ada@example.org"
    assert normalise_email("a+b+c@x.org") == "a@x.org"


def test_suppression_digest_is_a_full_keyed_hmac_of_the_trimmed_lower_case_address() -> None:
    import hmac

    from apps.core.privacy import normalise_suppression_email, suppression_digest

    pepper = secrets.token_urlsafe(16)
    with override_settings(PRIVACY_PEPPER=pepper):
        digest = suppression_digest("  Ada@Example.ORG ")
        expected = hmac.new(
            pepper.encode(), b"suppression\0ada@example.org", hashlib.sha256
        ).hexdigest()
        assert digest == expected
        assert len(digest) == 64
        assert suppression_digest("ada@example.org") == digest
        # Only trimmed and lower-cased: a +tag is another mailbox.
        assert normalise_suppression_email("Ada+news@X.org ") == "ada+news@x.org"
        assert suppression_digest("ada+news@example.org") != digest
    with override_settings(PRIVACY_PEPPER=secrets.token_urlsafe(16)):
        assert suppression_digest("ada@example.org") != digest
