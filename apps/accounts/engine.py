"""MFA building blocks (CORE.4). Pure functions, no ORM.

- Recovery codes: 16 Crockford base32 characters (80 bits), shown as `xxxx-xxxx-xxxx-xxxx`.
  Only the SHA-256 of the normalized code is stored: the code is random and high-entropy, so a
  slow password hash is not needed (same reasoning as API keys, CORE.6).
- Step-up: a verification is fresh while it is younger than a max age.
- Passkeys: thin wrappers around py_webauthn for the registration and authentication
  ceremonies, as a second factor (user verification preferred, "none" attestation).
- Secure action links (CORE.15): the claims a link token carries and the checks on them.
  Signing the token is Django's job and lives in `services`.
"""

import hashlib
import hmac
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


# --- Secure action links (CORE.15)

# Why a link is refused; stored in the audit entry, never shown to the visitor.
BAD_SIGNATURE: Final = "bad_signature"
UNKNOWN_PURPOSE: Final = "unknown_purpose"
EXPIRED: Final = "expired"
OBJECT_GONE: Final = "object_gone"
OLD_VERSION: Final = "old_version"
WRONG_RECIPIENT: Final = "wrong_recipient"
INACTIVE: Final = "inactive"
USED: Final = "used"
# No confirmed second factor: no passwordless sign-in, or whoever holds the link could enrol
# their own first factor (CORE.4 needs no other proof for it) and pass the step-up.
NO_SECOND_FACTOR: Final = "no_second_factor"
# Staff and superusers never get the passwordless sign-in: they sign in with their password and
# MFA, then continue (lane-a decision on the CORE.15 review).
STAFF_RECIPIENT: Final = "staff_recipient"
# A newer link was issued for the same object and recipient, or the recipient's password changed.
REVOKED: Final = "revoked"
ACTION_NONCE_BYTES: Final = 16  # 128 bits: the single-use key of a link


@dataclass(frozen=True)
class ActionClaims:
    """What a secure action link proves once its signature checks out."""

    purpose: str
    object_key: str
    recipient: str  # the recipient user's public id
    version: str
    nonce: str
    expires_at: int  # Unix time
    generation: int  # the issue count for (purpose, object, recipient); a reissue bumps it
    stamp: str  # a keyed digest of the recipient's password hash when the link was issued


_CLAIM_FIELDS: Final = {
    "p": "purpose",
    "o": "object_key",
    "r": "recipient",
    "v": "version",
    "n": "nonce",
    "a": "stamp",
}


def new_action_claims(
    *,
    purpose: str,
    object_key: str,
    recipient: str,
    version: object,
    now: float,
    max_age: int,
    generation: int,
    stamp: str,
) -> ActionClaims:
    """Claims for a new link, valid for `max_age` seconds from `now`, with a fresh nonce.

    `generation` and `stamp` bind it to the latest issue and to the recipient's current
    password: a reissue or a password change revokes it."""
    return ActionClaims(
        purpose=purpose,
        object_key=object_key,
        recipient=recipient,
        version=str(version),
        nonce=secrets.token_urlsafe(ACTION_NONCE_BYTES),
        expires_at=int(now) + max_age,
        generation=generation,
        stamp=stamp,
    )


def action_claims_payload(claims: ActionClaims) -> dict[str, str | int]:
    """The compact JSON object the signed token carries."""
    payload: dict[str, str | int] = {
        short: getattr(claims, name) for short, name in _CLAIM_FIELDS.items()
    }
    payload["e"] = claims.expires_at
    payload["g"] = claims.generation
    return payload


def action_claims_from_payload(payload: object) -> ActionClaims | None:
    """Claims from a verified token payload; None when the payload is not well formed."""
    if not isinstance(payload, dict):
        return None
    values = {name: payload.get(short) for short, name in _CLAIM_FIELDS.items()}
    expires_at, generation = payload.get("e"), payload.get("g")
    if not all(isinstance(value, str) for value in values.values()):
        return None
    if not all(_is_int(number) for number in (expires_at, generation)):
        return None
    return ActionClaims(expires_at=expires_at, generation=generation, **values)  # type: ignore[arg-type]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def action_link_refusal(
    claims: ActionClaims,
    *,
    now: float,
    current_version: str | None,
    recipient: str | None = None,
    current_generation: int | None = None,
    current_stamp: str | None = None,
) -> str | None:
    """Why the link cannot be used now, or None when it can.

    `current_version` is the object's version now (None: the object is gone); `recipient`, when
    given, is the public id of the user the link must belong to; `current_generation` and
    `current_stamp`, when given, are the latest issue for the object and recipient and the
    recipient's password stamp now. Comparisons run in constant time.
    """
    if now > claims.expires_at:
        return EXPIRED
    if current_version is None:
        return OBJECT_GONE
    if not _same(claims.version, current_version):
        return OLD_VERSION
    if current_generation is not None and claims.generation != current_generation:
        return REVOKED
    if current_stamp is not None and not _same(claims.stamp, current_stamp):
        return REVOKED
    if recipient is not None and not _same(claims.recipient, recipient):
        return WRONG_RECIPIENT
    return None


def action_nonce_digest(nonce: str) -> str:
    """SHA-256 of a link nonce: the key of its single-use record."""
    return hashlib.sha256(nonce.encode()).hexdigest()
