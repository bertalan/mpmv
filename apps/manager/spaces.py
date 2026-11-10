"""The Manager's two spaces, one URL prefix each (CORE.17, spec 02 §Backoffice).

Every URL under `/manager/` sits under exactly one of these prefixes: the gate
(`middleware.ManagerGateMiddleware`) answers 404 to anything else under `/manager/`, and the sweep
(`checks.manager_route_problems`) fails when a route is registered outside them.
"""

from enum import StrEnum
from typing import Final


class Space(StrEnum):
    SHOP = "shop"  # vendor members, on their selected vendor
    PLATFORM = "platform"  # platform staff, across vendors


MANAGER_PREFIX: Final = "/manager/"
PREFIXES: Final[dict[Space, str]] = {space: f"{MANAGER_PREFIX}{space}/" for space in Space}


def space_of(path: str) -> Space | None:
    """The space whose prefix `path` starts with, or None (outside both, the Manager root too)."""
    found = [space for space, prefix in PREFIXES.items() if path.startswith(prefix)]
    return found[0] if len(found) == 1 else None
