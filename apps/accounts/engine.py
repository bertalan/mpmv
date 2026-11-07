"""MFA building blocks (CORE.4). Pure functions, no ORM.

- Recovery codes: 16 Crockford base32 characters (80 bits), shown as `xxxx-xxxx-xxxx-xxxx`.
  Only the SHA-256 of the normalized code is stored: the code is random and high-entropy, so a
  slow password hash is not needed (same reasoning as API keys, CORE.6).
- Step-up: a verification is fresh while it is younger than a max age.
- Passkeys: thin wrappers around py_webauthn for the registration and authentication
  ceremonies, as a second factor (user verification preferred, "none" attestation).
"""

import hashlib
import json
import secrets
from base64 import b32encode, urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

RECOVERY_CODE_COUNT: Final = 10
RECOVERY_CODE_LENGTH: Final = 16
RECOVERY_CODE_GROUP: Final = 4
# Crockford base32: no I, L, O, U, so codes read back unambiguously.
RECOVERY_ALPHABET: Final = "0123456789abcdefghjkmnpqrstvwxyz"
_AMBIGUOUS: Final = str.maketrans({"i": "1", "l": "1", "o": "0"})
_KNOWN_TRANSPORTS: Final = frozenset(t.value for t in AuthenticatorTransport)


# --- Recovery codes


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    codes: set[str] = set()
    while len(codes) < count:
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        codes.add(
            "-".join(
                raw[i : i + RECOVERY_CODE_GROUP]
                for i in range(0, RECOVERY_CODE_LENGTH, RECOVERY_CODE_GROUP)
            )
        )
    return sorted(codes)


def normalize_recovery_code(raw: str) -> str:
    """Lower-case, drop separators and whitespace, read I/L as 1 and O as 0."""
    return "".join(ch for ch in raw.lower() if ch.isalnum()).translate(_AMBIGUOUS)


def hash_recovery_code(raw: str) -> str:
    return hashlib.sha256(normalize_recovery_code(raw).encode()).hexdigest()


# --- TOTP display


def totp_secret(key: bytes) -> str:
    """The base32 secret users type into an authenticator app when they cannot scan the QR."""
    return b32encode(key).decode().rstrip("=")


# --- Step-up


def is_fresh(verified_at: float | None, *, now: float, max_age: int) -> bool:
    """True when the last verification happened at most `max_age` seconds ago."""
    if verified_at is None or verified_at > now:
        return False
    return now - verified_at <= max_age


# --- Passkeys (WebAuthn)


class PasskeyError(ValueError):
    """A passkey ceremony failed: malformed payload, wrong challenge/origin, bad signature."""


@dataclass(frozen=True)
class RelyingParty:
    id: str  # the site's host name, e.g. "shop.example.com"
    name: str
    origin: str  # scheme://host[:port] the browser reports


@dataclass(frozen=True)
class PasskeyOptions:
    public_key: dict[str, Any]  # JSON for navigator.credentials.create/get({publicKey})
    challenge: bytes


@dataclass(frozen=True)
class RegisteredPasskey:
    credential_id: str  # base64url
    public_key: bytes  # COSE key
    sign_count: int
    aaguid: str
    transports: list[str]
    backed_up: bool


def b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def from_b64url(text: str) -> bytes:
    return urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _descriptors(credential_ids: Sequence[str]) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=from_b64url(cid)) for cid in credential_ids]


def passkey_registration_options(
    rp: RelyingParty,
    *,
    user_handle: bytes,
    user_name: str,
    display_name: str,
    exclude_credential_ids: Sequence[str],
) -> PasskeyOptions:
    options = generate_registration_options(
        rp_id=rp.id,
        rp_name=rp.name,
        user_id=user_handle,
        user_name=user_name,
        user_display_name=display_name or user_name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=_descriptors(exclude_credential_ids),
    )
    return PasskeyOptions(json.loads(options_to_json(options)), options.challenge)


def _transports(credential: Mapping[str, Any] | str) -> list[str]:
    payload = json.loads(credential) if isinstance(credential, str) else credential
    response = payload.get("response")
    transports = response.get("transports") if isinstance(response, Mapping) else None
    if not isinstance(transports, list):
        return []
    return [t for t in transports if isinstance(t, str) and t in _KNOWN_TRANSPORTS]


def verify_passkey_registration(
    rp: RelyingParty, credential: Mapping[str, Any] | str, *, challenge: bytes
) -> RegisteredPasskey:
    try:
        verified = verify_registration_response(
            credential=credential if isinstance(credential, str) else dict(credential),
            expected_challenge=challenge,
            expected_rp_id=rp.id,
            expected_origin=rp.origin,
        )
        transports = _transports(credential)
    except (WebAuthnException, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PasskeyError(str(exc)) from exc
    return RegisteredPasskey(
        credential_id=b64url(verified.credential_id),
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        aaguid=verified.aaguid,
        transports=transports,
        backed_up=verified.credential_backed_up,
    )


def passkey_authentication_options(
    rp: RelyingParty, *, credential_ids: Sequence[str]
) -> PasskeyOptions:
    options = generate_authentication_options(
        rp_id=rp.id,
        allow_credentials=_descriptors(credential_ids),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return PasskeyOptions(json.loads(options_to_json(options)), options.challenge)


def credential_id_from(credential: Mapping[str, Any]) -> str:
    """The credential id a browser response claims, to look the stored passkey up."""
    credential_id = credential.get("id")
    if not isinstance(credential_id, str) or not credential_id:
        raise PasskeyError("credential id missing")
    return credential_id


def verify_passkey_authentication(
    rp: RelyingParty,
    credential: Mapping[str, Any] | str,
    *,
    challenge: bytes,
    public_key: bytes,
    sign_count: int,
) -> int:
    """Check an assertion against the stored public key; return the new signature counter."""
    try:
        verified = verify_authentication_response(
            credential=credential if isinstance(credential, str) else dict(credential),
            expected_challenge=challenge,
            expected_rp_id=rp.id,
            expected_origin=rp.origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PasskeyError(str(exc)) from exc
    return verified.new_sign_count
