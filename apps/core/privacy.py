"""Keyed pseudonyms for counters that must not hold personal data in the clear.

`keyed_hash` is an HMAC-SHA256 keyed on `settings.PRIVACY_PEPPER` when set, else on
`SECRET_KEY`, so a leaked counter table cannot be reversed by hashing guessed addresses or IPs.
The pepper is declared in the settings (`MPMV_PRIVACY_PEPPER`); the `core.E901` check refuses a
missing one on LIVE and STAGE. `purpose` separates the uses: the same value gives different
digests for different purposes. `suppression_digest` is the full HMAC for the marketing
suppression list (L-3): trimmed, lower-cased address, no `+tag` removal.
`normalise_email` makes one mailbox one key (case and `+tag` removed); keep the raw address for
sending.
"""

import hashlib
import hmac

from django.conf import settings


def _digest(value: str, *, purpose: str) -> str:
    key = str(getattr(settings, "PRIVACY_PEPPER", "") or settings.SECRET_KEY).encode()
    message = f"{purpose}\0{value}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def keyed_hash(value: str, *, purpose: str) -> str:
    return _digest(value, purpose=purpose)[:32]


def normalise_suppression_email(address: str) -> str:
    """Trimmed and lower case, nothing else (L-3): `+tag` stays, it is a different mailbox."""
    return address.strip().lower()


def suppression_digest(address: str) -> str:
    """Full HMAC-SHA256 (64 hex) of the normalised address for the suppression list (L-3)."""
    return _digest(normalise_suppression_email(address), purpose="suppression")


def normalise_email(address: str) -> str:
    """Lower case, without the `+tag` of the local part (`A+x@X.org` gives `a@x.org`)."""
    local, sep, domain = address.strip().lower().rpartition("@")
    if not sep:
        return address.strip().lower()
    return f"{local.split('+', 1)[0]}@{domain}"
