"""Consent rules (SEO.5, SEO.9, owner decision D-10). Pure functions: no Django, no ORM.

Four categories: `necessary` (technical cookies, always on, never asked) and three that need a
choice (`preferences`, `statistics`, `marketing`). The visitor's choice travels in one cookie,
`mpmv_consent`, readable by the page script so gated tags can start without a round trip:

    v1.<banner version>.<consent id>.<granted categories joined by "-">

The consent id is a random identifier made for this browser; it carries nothing about the
visitor. A banner version other than the current one counts as "not decided", so a changed text
asks again. Consent Mode v2 signals are derived from the granted categories only.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

NECESSARY = "necessary"
PREFERENCES = "preferences"
STATISTICS = "statistics"
MARKETING = "marketing"

GATED_CATEGORIES = (PREFERENCES, STATISTICS, MARKETING)
ALL_CATEGORIES = (NECESSARY, *GATED_CATEGORIES)

ACCEPT_ALL = "accept_all"
REJECT_ALL = "reject_all"
CUSTOM = "custom"
ACTIONS = (ACCEPT_ALL, REJECT_ALL, CUSTOM)

# Bump when the banner text or the categories change: everyone is asked again.
BANNER_VERSION = "1"
COOKIE_NAME = "mpmv_consent"
COOKIE_FORMAT = "v1"
COOKIE_MAX_AGE = 180 * 24 * 60 * 60  # six months, then the choice is asked again

# Consent Mode v2: each signal follows one category. `security_storage` is a technical need.
SIGNAL_CATEGORY = {
    "ad_storage": MARKETING,
    "ad_user_data": MARKETING,
    "ad_personalization": MARKETING,
    "analytics_storage": STATISTICS,
    "functionality_storage": PREFERENCES,
    "personalization_storage": PREFERENCES,
    "security_storage": NECESSARY,
}
GRANTED = "granted"
DENIED = "denied"

_COOKIE = re.compile(
    rf"^{COOKIE_FORMAT}\.(?P<version>[A-Za-z0-9_]{{1,20}})\.(?P<id>[0-9a-f]{{32}})"
    r"\.(?P<categories>[a-z-]{0,64})$"
)


@dataclass(frozen=True)
class ConsentState:
    """What is known about a visitor's choice. Undecided means nothing gated may run."""

    decided: bool = False
    granted: frozenset[str] = frozenset()
    banner_version: str = ""
    consent_id: str = ""

    def allows(self, category: str) -> bool:
        return allows(self, category)


UNDECIDED = ConsentState()


def allows(state: ConsentState, category: str) -> bool:
    """`necessary` is always allowed; any other category only after a choice that granted it."""
    if category == NECESSARY:
        return True
    return state.decided and category in state.granted and category in GATED_CATEGORIES


def granted_for(action: str, selected: Iterable[str] = ()) -> frozenset[str]:
    """The categories a choice grants. Unknown actions and categories grant nothing."""
    if action == ACCEPT_ALL:
        return frozenset(GATED_CATEGORIES)
    if action == CUSTOM:
        return frozenset(c for c in selected if c in GATED_CATEGORIES)
    return frozenset()


def encode_cookie(state: ConsentState) -> str:
    """The cookie value of a decided state, categories in a fixed order."""
    categories = "-".join(c for c in GATED_CATEGORIES if c in state.granted)
    return f"{COOKIE_FORMAT}.{state.banner_version}.{state.consent_id}.{categories}"


def parse_cookie(value: str | None, *, banner_version: str = BANNER_VERSION) -> ConsentState:
    """The state a cookie value stands for. Hostile or stale values are "not decided"."""
    match = _COOKIE.match(value or "")
    if not match:
        return UNDECIDED
    if match["version"] != banner_version:
        return ConsentState(consent_id=match["id"])  # asked again; the id stays as the link
    granted = frozenset(c for c in match["categories"].split("-") if c in GATED_CATEGORIES)
    return ConsentState(True, granted, match["version"], match["id"])


def consent_signals(state: ConsentState) -> dict[str, str]:
    """Consent Mode v2 signals for the state; everything but `security_storage` is denied
    until a choice grants its category."""
    return {
        signal: GRANTED if allows(state, category) else DENIED
        for signal, category in SIGNAL_CATEGORY.items()
    }
