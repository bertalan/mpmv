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
