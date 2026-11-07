"""A software WebAuthn authenticator for tests (CORE.4).

It answers `navigator.credentials.create()` / `.get()` options the way a platform authenticator
does, with a fresh P-256 key, "none" attestation and a counter, so the server side of passkey
registration and authentication runs end to end through py_webauthn without a browser. Keys and
credential ids are generated at runtime.
"""

import hashlib
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

# Authenticator data flags (WebAuthn §6.1).
USER_PRESENT = 0x01
USER_VERIFIED = 0x04
ATTESTED_CREDENTIAL_DATA = 0x40


def b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def from_b64url(text: str) -> bytes:
    return urlsafe_b64decode(text + "=" * (-len(text) % 4))


class FakeAuthenticator:
    """One credential on one authenticator, bound to `origin` (and its host as RP ID)."""

    def __init__(self, origin: str, rp_id: str | None = None) -> None:
        self.origin = origin
        self.rp_id = rp_id or origin.split("://", 1)[1].split(":", 1)[0]
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.sign_count = 0
        self.user_handle: bytes | None = None

    @property
    def credential_id_b64(self) -> str:
        return b64url(self.credential_id)

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        payload = {
            "type": kind,
            "challenge": challenge,
            "origin": origin or self.origin,
            "crossOrigin": False,
        }
        return json.dumps(payload).encode()

    def _cose_public_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def create(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        """Registration credential JSON for `PublicKeyCredentialCreationOptions` JSON."""
        self.user_handle = from_b64url(options["user"]["id"])
        rp_id_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        flags = USER_PRESENT | USER_VERIFIED | ATTESTED_CREDENTIAL_DATA
        attested = (
            bytes(16)  # AAGUID: unknown authenticator
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        auth_data = rp_id_hash + bytes([flags]) + self.sign_count.to_bytes(4, "big") + attested
        attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation_object),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(
        self,
        options: dict[str, Any],
        *,
        origin: str | None = None,
        signing_key: ec.EllipticCurvePrivateKey | None = None,
    ) -> dict[str, Any]:
        """Authentication credential JSON for `PublicKeyCredentialRequestOptions` JSON."""
        self.sign_count += 1
        rp_id_hash = hashlib.sha256(options["rpId"].encode()).digest()
        flags = USER_PRESENT | USER_VERIFIED
        auth_data = rp_id_hash + bytes([flags]) + self.sign_count.to_bytes(4, "big")
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        key = signing_key or self.private_key
        signature = key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle) if self.user_handle else None,
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
