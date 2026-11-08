"""Image licence rules (CMS.8, spec 10 §10.2): pure functions, no ORM.

Every image comes from a free licence that allows commercial use, or is self-produced (owner
decision Q-63). The allow list (`ImageLicence`) is staff-editable, but it never takes a
non-commercial (NC) or no-derivatives (ND) licence. An image names its licence and author;
the licence entry says whether a source URL, a permission reference and a visible credit are
required.
"""

import re
from dataclasses import dataclass

# Licence codes and names are split on anything but letters and digits, so "vendor" never
# matches "nd"; Creative Commons URLs name the licence in the path ("/licenses/by-nc-sa/4.0/").
RESTRICTED_TOKENS = frozenset({"nc", "nd", "noncommercial", "noderivatives", "noderivs"})
RESTRICTED_NAME = re.compile(r"non[\s-]?commercial|no[\s-]?deriv", re.IGNORECASE)
RESTRICTED_ERROR = (
    "Non-commercial (NC) and no-derivatives (ND) licences cannot be added: images must allow "
    "commercial use and adaptation."
)


@dataclass(frozen=True)
class LicenceTerms:
    """What an allow-list entry requires of the images that use it."""

    code: str
    name: str
    url: str
    attribution_required: bool
    source_required: bool
    permission_required: bool


@dataclass(frozen=True)
class Credit:
    """The credit line of an image whose licence requires attribution."""

    text: str
    licence_name: str
    licence_url: str
    source_url: str


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


def restricted_licence_error(code: str, name: str, url: str) -> str | None:
    """Why this licence cannot be on the allow list, or None when it can."""
    tokens = _tokens(code) | _tokens(name) | _tokens(url)
    if tokens & RESTRICTED_TOKENS or RESTRICTED_NAME.search(name):
        return RESTRICTED_ERROR
    return None


def metadata_errors(
    licence: LicenceTerms | None, *, author: str, source_url: str, permission_ref: str
) -> dict[str, str]:
    """The missing metadata of an image, as {field: message}; empty when it is complete."""
    errors: dict[str, str] = {}
    if licence is None:
        errors["licence"] = "Choose the image's licence."
    if not author.strip():
        errors["author"] = "Name the author or rights holder to credit."
    if licence is not None and licence.source_required and not source_url.strip():
        errors["source_url"] = f"Give the URL the image comes from ({licence.name})."
    if licence is not None and licence.permission_required and not permission_ref.strip():
        errors["permission_ref"] = "Refer to the stored written permission."
    return errors


def credit_for(
    licence: LicenceTerms | None, *, author: str, source_url: str, credit_text: str
) -> Credit | None:
    """The credit to show, or None when the licence does not require attribution."""
    if licence is None or not licence.attribution_required:
        return None
    return Credit(
        text=credit_text.strip() or author.strip(),
        licence_name=licence.name,
        licence_url=licence.url,
        source_url=source_url.strip(),
    )
