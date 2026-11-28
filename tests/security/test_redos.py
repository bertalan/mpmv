"""SEC.5 attack scenarios, category "Resource abuse": ReDoS (owner security wave, item 10, 2b).

Every regular expression under `apps/` (and every routed URL pattern) runs against pumped,
never-matching inputs in a worker process with a time budget (`tests.security.redos`). The fast
sweep runs on every PR; the 100 KB sweep is marked `security_slow` and runs nightly
(`make test-security-nightly`). A new regex is swept automatically: nothing needs listing unless
its input is bounded before it reaches the pattern (INPUT_LIMITS, with the reason).
"""

import re

import pytest

from tests.security import redos

FAST_SIZE = 1_000  # characters per attack input on every PR: exponential blow-ups show up here
FAST_BUDGET = 0.1  # seconds for one operation
SLOW_SIZE = 100_000  # nightly: polynomial (quadratic) backtracking shows up here
SLOW_BUDGET = 0.5
HARD_LIMIT = 5.0  # one operation running longer kills the worker

GUNICORN_REQUEST_LINE = 4094  # gunicorn's default limit_request_line: the longest URL path

# Patterns whose input is bounded before it reaches them, swept at that bound instead of the
# full size. Each entry needs the reason the bound holds; a stale entry fails the suite.
INPUT_LIMITS: dict[str, tuple[int, str]] = {
    "urlconf:/django-admin/r/<path:content_type_id>/<path:object_id>/ "
    "django.contrib.contenttypes.views.shortcut": (
        GUNICORN_REQUEST_LINE,
        "Django's admin shortcut route; a URL path never exceeds the request line limit",
    ),
    "urlconf:/admin/snippets/cms/contentsnippet/history/(?P<pk>.+)/revisions/compare/"
    "(?P<revision_id_a>live|earliest|\\d+)\\.\\.\\.(?P<revision_id_b>live|latest|\\d+)/$ "
    "wagtail.snippets.views.snippets.RevisionsCompareView": (
        GUNICORN_REQUEST_LINE,
        "Wagtail's snippet revision compare route (third party, SEC.5); a URL path never "
        "exceeds the request line limit",
    ),
    "apps/core/observability.py:118": (
        40,
        "the caller cuts the text before a UUID to its last 40 characters before matching (SEC.5)",
    ),
    "apps/themes/csp_lint.py:19": (
        FAST_SIZE,
        "trusted input: lints the repository's own templates in CI (THM.3), never a request",
    ),
    "apps/themes/engine.py:508": (
        FAST_SIZE,
        "trusted input: minifies the repository's theme CSS at build time (THM.2)",
    ),
}


# Call sites whose pattern is built at runtime (an f-string, a loop). Each is swept through
# representative expansions listed here; a new dynamic call site fails the suite until it is
# listed with its reason, and a stale entry fails it too.
_IGNORECASE = 2
DYNAMIC: dict[str, tuple[str, list[tuple[str, int, str]]]] = {
    "apps/ingest/extraction.py:73": (
        "escaped literal words joined by |, inside word boundaries; input is a product text",
        [
            (
                r"\b(" + "|".join(re.escape(w) for w in ("a+b", "a.b", "ab", "(x)", "x")) + r")\b",
                0,
                "search",
            )
        ],
    ),
    "apps/ingest/extraction.py:158": (
        "three literal patterns tried in turn (a loop); copied here, input is a product text",
        [
            (
                r"(?:capienza|capacity)\s*l\.?\s*(\d+)(?:\s*[/-]\s*(\d+))?\s*(?:cad\.|cadauna|each)",
                0,
                "search",
            ),
            (r"capienza\s*lt\.?\s*(\d+)(?:\s*[/-]\s*(\d+))?", 0, "search"),
            (r"lt\.?\s*(\d+)(?:\s*[/-]\s*(\d+))?", 0, "search"),
        ],
    ),
    "apps/ingest/enrich_engine.py:47": (
        "an escaped literal term (spaces become \\s+) between two lookarounds; input is a "
        "product text",
        [(r"(?<![^\W_])eco\s+friendly(?![^\W_])", 0, "search")],
    ),
    "apps/themes/csp_lint.py:27": (
        "fixed template with a literal component name; trusted input (the repository's templates)",
        [(r"\sdata-bs-toggle\s*=\s*[\"']?\s*collapse\b", _IGNORECASE, "search")],
    ),
    "apps/themes/engine.py:613": (
        "fixed template with an escaped literal layer name; trusted input (theme CSS)",
        [(r"@layer\s+base\s*\{", 0, "search")],
    ),
}


def _dynamic() -> list[redos.Found]:
    return [
        redos.Found(pattern, flags, where, frozenset({operation}))
        for where, (_, expansions) in DYNAMIC.items()
        for pattern, flags, operation in expansions
    ]


def _sweep(size: int, budget: float) -> list[redos.Slow]:
    patterns = [*redos.collect_patterns()[0], *_dynamic()]
    groups: dict[int, list[redos.Found]] = {}
    for found in patterns:
        limit = INPUT_LIMITS.get(found.where, (size, ""))[0]
        groups.setdefault(min(size, limit), []).append(found)
    slow: list[redos.Slow] = []
    for group_size, group in sorted(groups.items()):
        slow += redos.sweep(group, size=group_size, budget=budget, hard_limit=HARD_LIMIT)
    return slow


@pytest.mark.security
def test_the_sweep_finds_every_regex_call_site() -> None:
    patterns, unresolved = redos.collect_patterns()
    # A dynamic pattern the sweep cannot see is listed in DYNAMIC, or becomes a module-level
    # compiled pattern.
    assert {site.where for site in unresolved} == set(DYNAMIC), unresolved
    places = {found.where.split(":")[0] for found in patterns}
    # Sanity: the scan reaches literals, module-level patterns, validators and routes.
    assert "urlconf" in places
    assert any(place.startswith("apps/") for place in places)
    assert redos.model_validator_scan()


@pytest.mark.security
def test_every_input_limit_names_a_collected_pattern() -> None:
    patterns, _ = redos.collect_patterns()
    assert set(INPUT_LIMITS) <= {found.where for found in patterns}


@pytest.mark.security
def test_the_sweep_catches_a_catastrophic_pattern() -> None:
    evil = redos.Found(r"^(a+)+$", 0, "probe:evil", frozenset({"fullmatch"}))
    linear = redos.Found(r"^[a-z]+$", 0, "probe:linear", frozenset({"fullmatch"}))
    slow = redos.sweep([evil, linear], size=FAST_SIZE, budget=FAST_BUDGET, hard_limit=1.0)
    assert [entry.found.where for entry in slow] == ["probe:evil"]
    assert slow[0].killed


@pytest.mark.security
def test_no_regex_backtracks_catastrophically_on_short_inputs() -> None:
    slow = _sweep(FAST_SIZE, FAST_BUDGET)
    assert not slow, redos.report(slow)


@pytest.mark.security
@pytest.mark.security_slow
def test_no_regex_backtracks_catastrophically_on_100kb_inputs() -> None:
    slow = _sweep(SLOW_SIZE, SLOW_BUDGET)
    assert not slow, redos.report(slow)
