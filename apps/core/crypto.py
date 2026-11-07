"""Field-level encryption primitives (SEC.3, spec 12): AES-256-GCM with versioned keys.

Pure functions, no Django and no ORM: `apps.core.encryption` loads the keys from the settings and
`apps.core.fields.EncryptedCharField` uses them. See docs/dev/field-encryption.md.

- A key is written `<key id>:<base64url of 32 random bytes>`. A keyring lists keys newest first:
  the first one (primary) encrypts, every key decrypts. Rotation = put a new key first, run
  `manage.py rotate_field_keys`, then retire the old key.
- A stored value is a token `enc1:<key id>:<base64url(nonce | ciphertext | tag)>`, with a random
  96-bit nonce per value. The column it belongs to (`app_label.model.field`) is the associated
  data, so a token copied into another column does not decrypt.
- Errors never contain key material or plaintext.
"""

import base64
import binascii
import re
import secrets
from collections.abc import Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc1"
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12
_KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


class KeyConfigurationError(ValueError):
    """The configured keys are missing or malformed (the message never shows a key)."""


class DecryptionError(Exception):
    """A stored value cannot be decrypted: wrong or missing key, tampering, or cleartext."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _check_key_id(key_id: str) -> None:
    if not _KEY_ID.fullmatch(key_id):
        raise KeyConfigurationError(
            f"Invalid field encryption key id {key_id!r}: use 1-32 letters, digits, '-' or '_'."
        )


def generate_key(key_id: str) -> str:
    """A new random key entry, `<key id>:<base64url key>`."""
    _check_key_id(key_id)
    key = base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")
    return f"{key_id}:{key}"


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX + ":")


def key_id_of(value: str) -> str | None:
    """The id of the key a token was encrypted with; None for anything else (cleartext)."""
    if not is_encrypted(value):
        return None
    parts = value.split(":")
    return parts[1] if len(parts) == 3 else None


class Keyring:
    """Keys by id, primary first. `repr` shows the ids only."""

    __slots__ = ("_ciphers", "key_ids")

    def __init__(self, keys: Iterable[tuple[str, bytes]]) -> None:
        pairs = list(keys)
        if not pairs:
            raise KeyConfigurationError("no field encryption key is configured")
        self.key_ids: tuple[str, ...] = tuple(key_id for key_id, _ in pairs)
        self._ciphers = {key_id: AESGCM(key) for key_id, key in pairs}

    @property
    def primary_id(self) -> str:
        return self.key_ids[0]

    def __repr__(self) -> str:
        return f"Keyring(key_ids={self.key_ids!r})"

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._ciphers[self.primary_id].encrypt(
            nonce, plaintext.encode("utf-8"), context.encode("utf-8")
        )
        return f"{PREFIX}:{self.primary_id}:{_b64encode(nonce + sealed)}"

    def decrypt(self, token: str, *, context: str) -> str:
        key_id = key_id_of(token)
        if key_id is None:
            raise DecryptionError(f"{context}: the stored value is not an {PREFIX} token")
        cipher = self._ciphers.get(key_id)
        if cipher is None:
            raise DecryptionError(f"{context}: key {key_id!r} is not configured")
        try:
            raw = _b64decode(token.rsplit(":", 1)[1])
            plain = cipher.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode("utf-8"))
        except (ValueError, binascii.Error, InvalidTag):
            raise DecryptionError(
                f"{context}: key {key_id!r} does not decrypt the stored value (wrong key, "
                "or the value was altered or moved from another column)"
            ) from None
        return plain.decode("utf-8")

    def needs_rotation(self, token: str) -> bool:
        """True for a token of an older key (or cleartext): `rotate_field_keys` rewrites it."""
        return key_id_of(token) != self.primary_id


def parse_keys(entries: Iterable[str]) -> Keyring:
    """Build a Keyring from `<key id>:<base64url key>` entries, newest (primary) first."""
    pairs: list[tuple[str, bytes]] = []
    for position, entry in enumerate(entries, start=1):
        key_id, separator, encoded = entry.strip().partition(":")
        if not separator:
            raise KeyConfigurationError(
                f"Field encryption key #{position} must be written '<key id>:<base64url key>'."
            )
        _check_key_id(key_id)
        try:
            key = _b64decode(encoded)
        except (ValueError, binascii.Error):
            key = b""
        if len(key) != KEY_BYTES:
            raise KeyConfigurationError(
                f"Field encryption key {key_id!r} must be {KEY_BYTES} random bytes, base64url."
            )
        if any(key_id == known for known, _ in pairs):
            raise KeyConfigurationError(f"Field encryption key id {key_id!r} is listed twice.")
        pairs.append((key_id, key))
    return Keyring(pairs)
