"""The redaction regexes run in the request thread: linear time, bounded input (OPS.2 re-check)."""

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable

import pytest

from apps.core.observability import MAX_TEXT, redact_text, redact_unbounded


def _fake(label: str, length: int) -> str:
    """A stable, credential-free-looking test value built at run time (no literal in the source)."""
    return hashlib.sha256(label.encode()).hexdigest()[:length]


_PW = _fake("pw", 12)  # derived at run time, stable across xdist workers
_T8 = _fake("t8", 8)


def _random_jwt() -> str:
    """A well-formed JWT shape built at run time (no literal token in the source)."""

    def part(data: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return ".".join(
        [part({"alg": "HS256"}), part({"sub": secrets.token_hex(4)}), secrets.token_urlsafe(32)]
    )


def _fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalnum() else c for c in text)


PATHOLOGICAL: dict[str, Callable[[int], str]] = {
    "underscores-equals": lambda n: "_" * n + "=",
    "digit-dash": lambda n: "1-" * (n // 2),
    "letter-dash": lambda n: "a-" * (n // 2),
    "at-signs": lambda n: "a@" * (n // 2),
    "long-local-part": lambda n: "a" * n,
    "dotted": lambda n: "a." * (n // 2),
    "iban-prefix": lambda n: "IT60" + "X" * n,
    "bearer": lambda n: "Bearer " + "a" * n,
    "bearer-spaces": lambda n: "Bearer" + " " * n,
    "tokens": lambda n: "token" * (n // 5),
    "token-equals-spaces": lambda n: "token=" + " " * n,
    "key-colon-spaces": lambda n: "password" + " " * n,
    "authorization": lambda n: "authorization: " + "a" * n,
    "url-credentials": lambda n: "://" + "a" * n,
    "equals-run": lambda n: "=" * n,
    "long-token-after-equals": lambda n: "x=" + "A" * n,
    "vat": lambda n: "vat" + " " * n,
    "vat-prefix": lambda n: "IT" + "1" * n,
    "digits": lambda n: "1" * n,
    "digit-space": lambda n: "1 " * (n // 2),
    "jwt": lambda n: "eyJ" * (n // 3),
    "jwt-run": lambda n: "eyJ" + "a" * n,
    "percent-email": lambda n: "a" * n + "%40",
    "quotes": lambda n: "password='" + "x" * n,
    "escaped-quotes": lambda n: 'password=\\"' + "x" * n,
    "action-links": lambda n: "/account/link/" * (n // 14),
    "sk": lambda n: "sk_" * (n // 3),
}


def _best(func: Callable[[str], str], text: str, runs: int = 7) -> float:
    """Best CPU time of several runs.

    CPU time (not wall time) ignores the moments a loaded CI runner deschedules the process;
    the minimum drops the remaining spikes (GC, cache misses).
    """
    func(text)  # warm-up: regex caches, allocator, branch predictors
    best = float("inf")
    for _ in range(runs):
        started = time.process_time()
        func(text)
        best = min(best, time.process_time() - started)
    return best


@pytest.mark.parametrize("name", sorted(PATHOLOGICAL))
def test_the_patterns_scale_linearly(name: str) -> None:
    """Input x4: linear code costs ~4x, quadratic ~16x, exponential far more. Allow up to 10x."""
    make = PATHOLOGICAL[name]
    small_text, large_text = make(3000), make(12000)
    message = ""
    # A real backtracking blow-up fails every attempt; a scheduler hiccup does not.
    for _ in range(3):
        small = _best(redact_unbounded, small_text)
        large = _best(redact_unbounded, large_text)
        # The floor only guards against a sub-0.5 ms baseline (timer noise).
        if large <= max(small, 0.0005) * 10:
            return
        message = f"{name}: {small:.4f}s -> {large:.4f}s"
    pytest.fail(message)


@pytest.mark.parametrize("name", sorted(PATHOLOGICAL))
def test_one_call_on_64_kb_stays_under_a_second(name: str) -> None:
    started = time.perf_counter()
    redact_text(PATHOLOGICAL[name](64 * 1024))
    assert time.perf_counter() - started < 1


def test_a_long_message_is_cut_before_matching_and_marked() -> None:
    out = redact_text("a" * (MAX_TEXT * 3))
    assert len(out) <= MAX_TEXT + 40
    assert out.endswith("[truncated]")


def test_a_short_message_is_not_marked() -> None:
    assert redact_text("short") == "short"


def test_a_secret_beyond_the_cut_is_not_logged() -> None:
    out = redact_text("x" * MAX_TEXT + f" password={_PW}")
    assert _PW not in out


@pytest.mark.parametrize(
    "text",
    [
        f'x="{{\\"password\\": \\"{_PW}\\"}}"',
        f'{{"msg": "{{\\"token\\": \\"{_T8}\\"}}"}}',
        f"passcode={_PW}",
        "otp=123456",
        f"key={_T8}",
        f"bearer: {_T8}1234",
        pytest.param("jwt " + _random_jwt(), id="jwt"),
        _fullwidth("IT60X0542811101000000123456"),
        _fullwidth(f"password={_PW}"),
        "card " + _fullwidth("4111 1111 1111 1111"),
    ],
)
def test_the_cheap_minors_are_redacted(text: str) -> None:
    out = redact_text(text)
    for leaked in (_PW, _T8, "123456", "SflKxw", "0542811101", "4111"):
        assert leaked not in out
