"""CORE.15: pure rules of secure action links (claims, expiry, binding checks). No database."""

import pytest

from apps.accounts import engine

NOW = 1_800_000_000.0
DAY = 24 * 3600


def claims(**overrides: object) -> engine.ActionClaims:
    values: dict[str, object] = {
        "purpose": "tests.approve",
        "object_key": "doc-1",
        "recipient": "user-x",
        "version": "3",
        "now": NOW,
        "max_age": 3 * DAY,
    }
    values.update(overrides)
    return engine.new_action_claims(**values)  # type: ignore[arg-type]


def test_new_claims_expire_after_max_age_and_carry_a_random_nonce() -> None:
    first, second = claims(), claims()
    assert first.expires_at == int(NOW) + 3 * DAY
    assert first.nonce != second.nonce
    assert len(first.nonce) >= 22  # at least 128 bits


def test_versions_are_compared_as_text() -> None:
    assert claims(version=3).version == "3"


def test_claims_round_trip_through_their_payload() -> None:
    original = claims()
    assert engine.action_claims_from_payload(engine.action_claims_payload(original)) == original


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "text",
        {},
        {"p": "tests.approve", "o": "doc-1", "r": "user-x", "v": "3", "n": "abc"},  # no expiry
        {"p": 1, "o": "doc-1", "r": "user-x", "v": "3", "n": "abc", "e": 1},
        {"p": "tests.approve", "o": "doc-1", "r": "user-x", "v": "3", "n": "abc", "e": "1"},
        {"p": "tests.approve", "o": "doc-1", "r": "user-x", "v": "3", "n": "abc", "e": True},
    ],
)
def test_malformed_payloads_give_no_claims(payload: object) -> None:
    assert engine.action_claims_from_payload(payload) is None


def test_a_valid_link_has_no_refusal() -> None:
    assert engine.action_link_refusal(claims(), now=NOW, current_version="3") is None


def test_an_expired_link_is_refused() -> None:
    link = claims()
    refusal = engine.action_link_refusal(link, now=link.expires_at + 1, current_version="3")
    assert refusal == engine.EXPIRED
    assert engine.action_link_refusal(link, now=link.expires_at, current_version="3") is None


def test_an_old_version_is_refused() -> None:
    refusal = engine.action_link_refusal(claims(), now=NOW, current_version="4")
    assert refusal == engine.OLD_VERSION


def test_a_link_to_a_vanished_object_is_refused() -> None:
    refusal = engine.action_link_refusal(claims(), now=NOW, current_version=None)
    assert refusal == engine.OBJECT_GONE


def test_the_recipient_must_match_when_one_is_given() -> None:
    link = claims()
    ok = engine.action_link_refusal(link, now=NOW, current_version="3", recipient="user-x")
    assert ok is None
    wrong = engine.action_link_refusal(link, now=NOW, current_version="3", recipient="user-y")
    assert wrong == engine.WRONG_RECIPIENT


def test_nonce_digests_are_stable_hex_and_hide_the_nonce() -> None:
    digest = engine.action_nonce_digest("abc")
    assert digest == engine.action_nonce_digest("abc")
    assert len(digest) == 64
    assert "abc" not in digest


# --- Email-bound links (VEN.24: a recipient that has no account yet)


def test_an_email_recipient_is_a_digest_of_the_normalized_address() -> None:
    digest = engine.email_recipient("  Legal@Example.COM ")
    assert digest == engine.email_recipient("legal@example.com")
    assert "example" not in digest
    assert len(digest) == 64


def test_different_addresses_give_different_recipients() -> None:
    assert engine.email_recipient("a@example.com") != engine.email_recipient("b@example.com")


def test_an_email_bound_link_is_refused_for_another_address() -> None:
    issued = claims(recipient=engine.email_recipient("legal@example.com"))
    other = engine.email_recipient("someone@example.com")
    assert engine.action_link_refusal(issued, now=NOW, current_version="3", recipient=other) == (
        engine.WRONG_RECIPIENT
    )
    assert (
        engine.action_link_refusal(
            issued,
            now=NOW,
            current_version="3",
            recipient=engine.email_recipient("legal@example.com"),
        )
        is None
    )
