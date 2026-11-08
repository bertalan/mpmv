"""Postcode patterns (SHP.12): one grammar for shipping zones, zone resolution (SHP.2) and the
configuration import (SHP.13). Pure functions, no ORM.

A pattern is one of:

- `PREFIX*`: every postcode starting with the prefix (`BT*`, `846*`);
- `LOW..HIGH`: postcodes whose first N characters are between the bounds, inclusive; both bounds
  have N characters and LOW <= HIGH (`70000..74999`, `9000..9999`, `1000-001..1999-999`);
- `POSTCODE`: that postcode exactly (`84600`, `9000-001`).

Any of them may start with an ISO country code and a colon (`PT:9000..9999`, `GR:84*`): the
pattern then applies to that country only. A zone that covers several countries must qualify
every entry, because the same digits mean different places in different countries.

Postcodes and bounds are compared upper case with spaces and dashes removed (`846 00` = `84600`).
The range separator is `..` because postcodes of some countries contain a dash (PT `1000-001`,
PL `00-950`), which made the SHP.1 form `70000-74999` ambiguous; that form is refused.
"""

import re
from dataclasses import dataclass, replace
from typing import Final, Literal

from apps.core.countries import InvalidCountry, normalize_country

RANGE_SEPARATOR: Final = ".."
WILDCARD: Final = "*"
MAX_PATTERN_LENGTH: Final = 32

_TEXT = r"[A-Z0-9][A-Z0-9 -]*"
_EXACT = re.compile(_TEXT, re.ASCII)
_PREFIX = re.compile(rf"({_TEXT})\*", re.ASCII)
_RANGE = re.compile(rf"({_TEXT})\.\.\s*({_TEXT})", re.ASCII)
# Two numbers of the same length joined by a dash, spaced or not: the SHP.1 range syntax.
_OLD_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)", re.ASCII)
_QUALIFIER = re.compile(r"\s*([A-Za-z]{2})\s*:(.*)", re.ASCII | re.DOTALL)
_IGNORED = re.compile(r"[\s-]+")

Kind = Literal["exact", "prefix", "range"]


class InvalidPostcodePattern(ValueError):
    """Raised for text that is not a postcode pattern of the grammar above."""


@dataclass(frozen=True)
class PostcodePattern:
    """A parsed pattern: `low` is the postcode or prefix; `high` is set for ranges only."""

    kind: Kind
    low: str
    high: str = ""
    country: str = ""  # empty: not qualified, applies to any country of its zone

    def matches(self, postcode: str) -> bool:
        return self.matches_normalized(normalize_postcode(postcode))

    def matches_normalized(self, code: str) -> bool:
        """`matches` for a postcode already passed through `normalize_postcode`."""
        if not code:
            return False
        if self.kind == "exact":
            return code == self.low
        if self.kind == "prefix":
            return code.startswith(self.low)
        head = code[: len(self.low)]
        return len(head) == len(self.low) and self.low <= head <= self.high


def normalize_postcode(raw: str) -> str:
    """Upper case, without spaces and dashes: the form postcodes are compared in."""
    return _IGNORED.sub("", raw).upper()


def _canonical(raw: str) -> str:
    return " ".join(raw.strip().upper().split())


def _refuse(raw: str, reason: str) -> InvalidPostcodePattern:
    return InvalidPostcodePattern(
        f"{raw!r} is not a postcode pattern: {reason}. Write an exact postcode (84600), a prefix "
        "ending in '*' (BT*) or a range with '..' (70000..74999)."
    )


def split_country_qualifier(raw: str) -> tuple[str, str]:
    """`("PT", "9000..9999")` for `PT:9000..9999`; `("", raw)` when there is no `CC:` prefix.
    The country is normalised (`EL` -> `GR`); raises InvalidPostcodePattern for an unknown one.
    Shared with the province codes of a zone."""
    match = _QUALIFIER.fullmatch(raw)
    if match is None:
        return "", raw
    try:
        return normalize_country(match.group(1)), match.group(2).strip()
    except InvalidCountry as exc:
        raise InvalidPostcodePattern(f"{raw!r}: {exc}.") from exc


def parse_postcode_pattern(raw: str) -> PostcodePattern:
    """Parse and validate one pattern; raises InvalidPostcodePattern."""
    country, body = split_country_qualifier(raw)
    pattern = _parse_unqualified(body)
    return replace(pattern, country=country) if country else pattern


def _parse_unqualified(raw: str) -> PostcodePattern:
    text = _canonical(raw)
    if not text:
        raise _refuse(raw, "it is empty")
    if len(text) > MAX_PATTERN_LENGTH:
        raise _refuse(raw, f"it is longer than {MAX_PATTERN_LENGTH} characters")
    if RANGE_SEPARATOR in text:
        match = _RANGE.fullmatch(text)
        if match is None:
            raise _refuse(raw, "a range needs one '..' between two bounds, without '*'")
        low, high = (normalize_postcode(bound) for bound in match.groups())
        if not low or not high:
            raise _refuse(raw, "a range needs two bounds")
        if len(low) != len(high):
            raise _refuse(raw, "the bounds of a range must have the same length")
        if low > high:
            raise _refuse(raw, "the lower bound is above the upper one")
        return PostcodePattern("range", low, high)
    if text.endswith(WILDCARD):
        match = _PREFIX.fullmatch(text)
        if match is None:
            raise _refuse(raw, "'*' may only end a prefix")
        return PostcodePattern("prefix", normalize_postcode(match.group(1)))
    if _EXACT.fullmatch(text) is None:
        raise _refuse(raw, "only letters, digits, spaces and '-' are allowed")
    old = _OLD_RANGE.fullmatch(text)
    if old is not None and len(old.group(1)) == len(old.group(2)):
        raise InvalidPostcodePattern(
            f"{raw!r} is ambiguous: write a range as {old.group(1)}..{old.group(2)}, or the "
            "postcode without the dash."
        )
    return PostcodePattern("exact", normalize_postcode(text))


def normalize_postcode_pattern(raw: str) -> str:
    """The pattern as stored: validated, upper case, single spaces, no spaces around `..`."""
    parse_postcode_pattern(raw)
    country, body = split_country_qualifier(raw)
    text = _canonical(body)
    if RANGE_SEPARATOR in text:
        low, high = text.split(RANGE_SEPARATOR)
        text = f"{low.strip()}{RANGE_SEPARATOR}{high.strip()}"
    return f"{country}:{text}" if country else text


def match_postcode(pattern: str, postcode: str) -> bool:
    """True when `postcode` matches `pattern`; an empty postcode matches nothing."""
    return parse_postcode_pattern(pattern).matches(postcode)
