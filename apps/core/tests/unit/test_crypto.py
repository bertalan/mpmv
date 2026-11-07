"""SEC.3: field-level encryption primitives (AES-256-GCM with versioned, rotatable keys).

Keys are generated at runtime: no key material is ever written in the repository.
"""

import base64

import pytest

from apps.core.crypto import (
    PREFIX,
    DecryptionError,
    KeyConfigurationError,
    generate_key,
    is_encrypted,
    key_id_of,
    parse_keys,
)

CONTEXT = "vendors.vendor.iban"
PLAINTEXT = "IT60X0542811101000000123456"


def key_material(entry: str) -> str:
    return entry.split(":", 1)[1]


def test_a_value_round_trips_and_the_token_names_the_primary_key() -> None:
    keyring = parse_keys([generate_key("k1")])
    token = keyring.encrypt(PLAINTEXT, context=CONTEXT)
    assert token.startswith(f"{PREFIX}:k1:")
    assert PLAINTEXT not in token
    assert is_encrypted(token)
    assert key_id_of(token) == "k1"
    assert keyring.decrypt(token, context=CONTEXT) == PLAINTEXT


def test_encrypting_twice_gives_different_tokens() -> None:
    keyring = parse_keys([generate_key("k1")])
    assert keyring.encrypt(PLAINTEXT, context=CONTEXT) != keyring.encrypt(
        PLAINTEXT, context=CONTEXT
    )


def test_non_ascii_text_round_trips() -> None:
    keyring = parse_keys([generate_key("k1")])
    text = "Ünïcødé – 12/345/67890"
    assert keyring.decrypt(keyring.encrypt(text, context=CONTEXT), context=CONTEXT) == text


def test_after_rotation_the_new_key_encrypts_and_the_old_one_still_decrypts() -> None:
    old = generate_key("k1")
    before = parse_keys([old]).encrypt(PLAINTEXT, context=CONTEXT)
    rotated = parse_keys([generate_key("k2"), old])
    assert rotated.primary_id == "k2"
    assert rotated.key_ids == ("k2", "k1")
    assert rotated.decrypt(before, context=CONTEXT) == PLAINTEXT
    assert rotated.needs_rotation(before)
    after = rotated.encrypt(PLAINTEXT, context=CONTEXT)
    assert key_id_of(after) == "k2"
    assert not rotated.needs_rotation(after)


def test_a_wrong_key_with_the_same_id_fails_loudly() -> None:
    token = parse_keys([generate_key("k1")]).encrypt(PLAINTEXT, context=CONTEXT)
    with pytest.raises(DecryptionError, match="k1"):
        parse_keys([generate_key("k1")]).decrypt(token, context=CONTEXT)


def test_an_unknown_key_id_fails_loudly() -> None:
    token = parse_keys([generate_key("k1")]).encrypt(PLAINTEXT, context=CONTEXT)
    with pytest.raises(DecryptionError, match="k1"):
        parse_keys([generate_key("k2")]).decrypt(token, context=CONTEXT)


def test_a_token_is_bound_to_its_column() -> None:
    keyring = parse_keys([generate_key("k1")])
    token = keyring.encrypt(PLAINTEXT, context=CONTEXT)
    with pytest.raises(DecryptionError):
        keyring.decrypt(token, context="vendors.vendor.tax_code")


def test_a_tampered_token_fails() -> None:
    keyring = parse_keys([generate_key("k1")])
    token = keyring.encrypt(PLAINTEXT, context=CONTEXT)
    prefix, payload = token.rsplit(":", 1)
    raw = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw[-1] ^= 1
    tampered = prefix + ":" + base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    with pytest.raises(DecryptionError):
        keyring.decrypt(tampered, context=CONTEXT)


@pytest.mark.parametrize("value", ["IT60X0542811101000000123456", "enc1:k1", "enc1:k1:!!!", ""])
def test_cleartext_or_malformed_tokens_are_refused(value: str) -> None:
    with pytest.raises(DecryptionError):
        parse_keys([generate_key("k1")]).decrypt(value, context=CONTEXT)


def test_cleartext_is_not_reported_as_encrypted() -> None:
    assert not is_encrypted(PLAINTEXT)
    assert key_id_of(PLAINTEXT) is None


def test_no_key_is_a_configuration_error() -> None:
    with pytest.raises(KeyConfigurationError, match="no field encryption key"):
        parse_keys([])


@pytest.mark.parametrize(
    "entry",
    [
        "no-separator",
        "bad id:" + "A" * 43,
        ":" + "A" * 43,
        "k1:not base64!",
        "k1:" + base64.urlsafe_b64encode(b"short").decode(),
    ],
)
def test_malformed_keys_are_configuration_errors(entry: str) -> None:
    with pytest.raises(KeyConfigurationError):
        parse_keys([entry])


def test_duplicate_key_ids_are_refused() -> None:
    with pytest.raises(KeyConfigurationError, match="k1"):
        parse_keys([generate_key("k1"), generate_key("k1")])


def test_errors_and_repr_never_show_key_material() -> None:
    good = generate_key("k1")
    short = "k2:" + base64.urlsafe_b64encode(b"0123456789").decode()
    with pytest.raises(KeyConfigurationError) as excinfo:
        parse_keys([good, short])
    assert key_material(short) not in str(excinfo.value)
    assert key_material(good) not in str(excinfo.value)
    keyring = parse_keys([good])
    assert key_material(good) not in repr(keyring)
    assert key_material(good) not in str(keyring)


def test_generated_keys_are_random_and_valid() -> None:
    first, second = generate_key("k1"), generate_key("k1")
    assert first != second
    assert len(base64.urlsafe_b64decode(key_material(first))) == 32
    with pytest.raises(KeyConfigurationError):
        generate_key("not a valid id")
