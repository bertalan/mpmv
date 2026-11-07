"""Pure helpers for HTTP security headers that Django does not build itself (SEC.1)."""

from collections.abc import Mapping, Sequence

# Allowlist members written bare in a Permissions-Policy header; anything else is an origin.
_KEYWORDS = frozenset({"self", "src"})


def _allowlist_item(member: str) -> str:
    return member if member in _KEYWORDS else f'"{member}"'


def build_permissions_policy(policy: Mapping[str, Sequence[str]]) -> str:
    """Render {feature: allowlist} as a Permissions-Policy header value.

    An empty allowlist denies the feature everywhere (``camera=()``), ``["*"]`` allows every
    origin, ``"self"`` is a keyword and any other member is a quoted origin.
    """
    directives = []
    for feature, allowlist in policy.items():
        if list(allowlist) == ["*"]:
            directives.append(f"{feature}=*")
        else:
            members = " ".join(_allowlist_item(member) for member in allowlist)
            directives.append(f"{feature}=({members})")
    return ", ".join(directives)
