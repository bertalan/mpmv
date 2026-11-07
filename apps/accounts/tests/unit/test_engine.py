"""CORE.4: pure MFA logic (recovery codes, step-up freshness, passkey ceremonies). No database."""

import re
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from apps.accounts import engine
from apps.accounts.tests.fake_authenticator import FakeAuthenticator, b64url

ORIGIN = "https://shop.example.com"
RP = engine.RelyingParty(id="shop.example.com", name="MPMV", origin=ORIGIN)
CODE_RE = re.compile(r"^[0-9a-hjkmnp-tv-z]{4}(-[0-9a-hjkmnp-tv-z]{4}){3}$")


# --- Recovery codes


def test_recovery_codes_are_ten_unique_codes_with_80_bits_each() -> None:
    codes = engine.generate_recovery_codes()
    assert len(codes) == engine.RECOVERY_CODE_COUNT == 10
    assert len(set(codes)) == 10
    assert all(CODE_RE.match(code) for code in codes)


def test_recovery_codes_differ_between_calls() -> None:
    assert set(engine.generate_recovery_codes()).isdisjoint(engine.generate_recovery_codes())


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("abcd-efgh-jkmn-pqrs", "abcdefghjkmnpqrs"),
        ("  ABCD EFGH JKMN PQRS ", "abcdefghjkmnpqrs"),
        ("o0il-1111-0000-abcd", "001111110000abcd"),
    ],
)
def test_normalize_recovery_code_ignores_case_separators_and_ambiguous_letters(
    typed: str, expected: str
) -> None:
    assert engine.normalize_recovery_code(typed) == expected


def test_recovery_code_hash_is_sha256_of_the_normalized_code() -> None:
    code = engine.generate_recovery_codes(1)[0]
    digest = engine.hash_recovery_code(code)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert code.replace("-", "") not in digest
    assert engine.hash_recovery_code(code.upper().replace("-", " ")) == digest


# --- Step-up freshness


@pytest.mark.parametrize(
    ("verified_at", "now", "max_age", "fresh"),
    [
        (None, 1_000.0, 300, False),
        (1_000.0, 1_000.0, 300, True),
        (1_000.0, 1_300.0, 300, True),
        (1_000.0, 1_300.1, 300, False),
        (1_000.0, 1_060.0, 30, False),
        (2_000.0, 1_000.0, 300, False),  # a timestamp from the future is never trusted
    ],
)
def test_is_fresh(verified_at: float | None, now: float, max_age: int, fresh: bool) -> None:
    assert engine.is_fresh(verified_at, now=now, max_age=max_age) is fresh


# --- Passkeys (WebAuthn) with a software authenticator


def _register(authenticator: FakeAuthenticator) -> engine.RegisteredPasskey:
    options = engine.passkey_registration_options(
        RP,
        user_handle=b"\x01" * 16,
        user_name="staff@example.com",
        display_name="Staff",
        exclude_credential_ids=[],
    )
    credential = authenticator.create(options.public_key)
    return engine.verify_passkey_registration(RP, credential, challenge=options.challenge)


def test_registration_options_carry_rp_user_and_a_fresh_challenge() -> None:
    existing = b64url(b"\x02" * 32)
    options = engine.passkey_registration_options(
        RP,
        user_handle=b"\x01" * 16,
        user_name="staff@example.com",
        display_name="Staff",
        exclude_credential_ids=[existing],
    )
    public_key: dict[str, Any] = options.public_key
    assert public_key["rp"] == {"id": "shop.example.com", "name": "MPMV"}
    assert public_key["user"]["name"] == "staff@example.com"
    assert public_key["challenge"] == b64url(options.challenge)
    assert len(options.challenge) >= 32
    assert [c["id"] for c in public_key["excludeCredentials"]] == [existing]
    assert public_key["attestation"] == "none"


def test_a_passkey_registers_with_the_issued_challenge() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    passkey = _register(authenticator)
    assert passkey.credential_id == authenticator.credential_id_b64
    assert passkey.sign_count == 0
    assert passkey.transports == ["internal"]
    assert passkey.public_key


def test_registration_with_another_challenge_is_rejected() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    options = engine.passkey_registration_options(
        RP,
        user_handle=b"\x01" * 16,
        user_name="a@example.com",
        display_name="",
        exclude_credential_ids=[],
    )
    credential = authenticator.create(options.public_key)
    with pytest.raises(engine.PasskeyError):
        engine.verify_passkey_registration(RP, credential, challenge=b"\x00" * 32)


def test_registration_from_another_origin_is_rejected() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    options = engine.passkey_registration_options(
        RP,
        user_handle=b"\x01" * 16,
        user_name="a@example.com",
        display_name="",
        exclude_credential_ids=[],
    )
    credential = authenticator.create(options.public_key, origin="https://evil.example.net")
    with pytest.raises(engine.PasskeyError):
        engine.verify_passkey_registration(RP, credential, challenge=options.challenge)


def test_malformed_registration_payload_is_a_passkey_error() -> None:
    with pytest.raises(engine.PasskeyError):
        engine.verify_passkey_registration(RP, {"id": "x"}, challenge=b"\x00" * 32)


def test_a_registered_passkey_authenticates_and_advances_the_counter() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    passkey = _register(authenticator)
    options = engine.passkey_authentication_options(RP, credential_ids=[passkey.credential_id])
    assert options.public_key["rpId"] == "shop.example.com"
    assert [c["id"] for c in options.public_key["allowCredentials"]] == [passkey.credential_id]
    assertion = authenticator.get(options.public_key)
    assert engine.credential_id_from(assertion) == passkey.credential_id
    new_count = engine.verify_passkey_authentication(
        RP,
        assertion,
        challenge=options.challenge,
        public_key=passkey.public_key,
        sign_count=passkey.sign_count,
    )
    assert new_count == 1


def test_an_assertion_signed_by_another_key_is_rejected() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    passkey = _register(authenticator)
    options = engine.passkey_authentication_options(RP, credential_ids=[passkey.credential_id])
    forged = authenticator.get(
        options.public_key, signing_key=ec.generate_private_key(ec.SECP256R1())
    )
    with pytest.raises(engine.PasskeyError):
        engine.verify_passkey_authentication(
            RP, forged, challenge=options.challenge, public_key=passkey.public_key, sign_count=0
        )


def test_an_assertion_for_another_challenge_is_rejected() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    passkey = _register(authenticator)
    options = engine.passkey_authentication_options(RP, credential_ids=[passkey.credential_id])
    assertion = authenticator.get(options.public_key)
    with pytest.raises(engine.PasskeyError):
        engine.verify_passkey_authentication(
            RP, assertion, challenge=b"\x00" * 32, public_key=passkey.public_key, sign_count=0
        )


def test_a_counter_that_goes_backwards_is_rejected_as_a_cloned_authenticator() -> None:
    authenticator = FakeAuthenticator(ORIGIN)
    passkey = _register(authenticator)
    options = engine.passkey_authentication_options(RP, credential_ids=[passkey.credential_id])
    assertion = authenticator.get(options.public_key)  # counter 1
    with pytest.raises(engine.PasskeyError):
        engine.verify_passkey_authentication(
            RP, assertion, challenge=options.challenge, public_key=passkey.public_key, sign_count=5
        )


def test_credential_id_from_a_payload_without_id_is_a_passkey_error() -> None:
    with pytest.raises(engine.PasskeyError):
        engine.credential_id_from({"type": "public-key"})
