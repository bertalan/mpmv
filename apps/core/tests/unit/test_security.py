"""SEC.1: building the Permissions-Policy header value (pure function)."""

from apps.core.security import build_permissions_policy


def test_empty_allowlist_denies_the_feature_everywhere() -> None:
    assert build_permissions_policy({"camera": [], "microphone": []}) == "camera=(), microphone=()"


def test_self_keyword_is_not_quoted_and_origins_are() -> None:
    policy = {"payment": ["self", "https://pay.example.com"]}
    assert build_permissions_policy(policy) == 'payment=(self "https://pay.example.com")'


def test_wildcard_allows_every_origin() -> None:
    assert build_permissions_policy({"fullscreen": ["*"]}) == "fullscreen=*"


def test_empty_policy_builds_an_empty_header_value() -> None:
    assert build_permissions_policy({}) == ""
