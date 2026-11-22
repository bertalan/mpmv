"""Pure rules of the fitment scheme and its nodes (D-38, spec 17 §17.2). No Django, no database.

Names are compared after NFC, case and punctuation folding, so "Model-X" and "model x" are one
name. A scheme has one to four node levels, in a fixed order; nodes sit at the depth of their
level. The matching of products to
profiles and the rules of a product's declaration (FIT.2) live here too, as pure functions
over node paths: a path starting with another is below it.
"""

import calendar
import datetime as dt
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

MAX_LEVELS = 4

PROPOSED = "proposed"
ACTIVE = "active"
RETIRED = "retired"
STATUSES = (PROPOSED, ACTIVE, RETIRED)

_ALLOWED_TRANSITIONS = {(PROPOSED, ACTIVE), (ACTIVE, RETIRED), (RETIRED, ACTIVE)}
_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)
_UNDERSCORE = re.compile(r"_+")
_DIGITS = re.compile(r"\d+")
STEP_LENGTH = 4  # treebeard's default path step: one segment per tree level


def clean_name(text: str) -> str:
    """The name as stored: NFC, surrounding space removed, inner runs of space made single."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def normalize_name(text: str) -> str:
    """The comparison form of a name: NFC, case-folded, punctuation and space runs as one space."""
    folded = unicodedata.normalize("NFC", text).casefold()
    folded = _UNDERSCORE.sub(" ", folded)
    return _NOT_WORD.sub(" ", folded).strip()


def natural_sort_key(text: str) -> str:
    """A string that sorts "Model 9" before "Model 10": digit runs are zero-padded."""
    normalized = normalize_name(text)
    return _DIGITS.sub(lambda m: m.group().zfill(10), normalized)


def name_clash(name: str, existing: Mapping[str, int]) -> int | None:
    """The id of the entry in `existing` ({display name: id}) that `name` equals, or None."""
    wanted = normalize_name(name)
    for other, key in existing.items():
        if normalize_name(other) == wanted:
            return key
    return None


def add_level_error(positions: Sequence[int]) -> str | None:
    if len(positions) >= MAX_LEVELS:
        return f"A scheme has at most {MAX_LEVELS} levels."
    return None


def remove_level_error(
    codes: Sequence[str], code: str, nodes_per_level: Mapping[str, int]
) -> str | None:
    """A level is removed only from the end, and only when no node sits on it."""
    if code not in codes:
        return "This level does not exist."
    if codes[-1] != code:
        return "Only the last level can be removed."
    if nodes_per_level.get(code, 0):
        return "This level has nodes: merge or delete them first."
    return None


def reorder_error(
    current: Sequence[str], new: Sequence[str], nodes_per_level: Mapping[str, int]
) -> str | None:
    """The new order must list the same levels, and no level that moves may have nodes."""
    if sorted(current) != sorted(new) or len(set(new)) != len(new):
        return "The new order must list every level exactly once."
    moved = [code for old, code in zip(current, new, strict=True) if old != code]
    if any(nodes_per_level.get(code, 0) for code in moved):
        return "A level with nodes cannot move: merge or retire its nodes first."
    return None


def merge_error(
    source_path: str, target_path: str, source_depth: int, target_depth: int
) -> str | None:
    if source_path == target_path:
        return "A node cannot be merged into itself."
    if source_depth != target_depth:
        return "Only nodes of the same level can be merged."
    if target_path.startswith(source_path):
        return "A node cannot be merged into its own subtree."
    return None


def status_transition_error(old: str, new: str) -> str | None:
    if (old, new) in _ALLOWED_TRANSITIONS:
        return None
    return f"A {old} node cannot become {new}."


# --- Matching a profile against a product's declaration (FIT.2, spec 17 §17.2.3)

UNDECLARED = "undeclared"
SPECIFIC = "specific"
UNIVERSAL = "universal"
MODES = (UNDECLARED, SPECIFIC, UNIVERSAL)

FITS = "fits"
CHECK = "check"
NO = "no"
UNKNOWN = "unknown"

DEFAULT_MAX_ROWS = 500


@dataclass(frozen=True)
class Row:
    """One declaration row: a node path, a flag, and an optional year range (both included)."""

    node_path: str
    is_exclusion: bool = False
    year_from: int | None = None
    year_to: int | None = None


@dataclass(frozen=True)
class Profile:
    """What the customer chose: a node path and, optionally, a year."""

    node_path: str
    year: int | None = None


def _covers_node(row: Row, profile: Profile) -> bool:
    """The row's node is the profile's node or one of its ancestors."""
    return profile.node_path.startswith(row.node_path)


def _below_node(row: Row, profile: Profile) -> bool:
    """The row names something more specific than the profile."""
    return row.node_path.startswith(profile.node_path) and row.node_path != profile.node_path


def _year_state(row: Row, year: int | None) -> str:
    """`in`, `out`, or `unknown` (the row has a range and the profile has no year)."""
    if row.year_from is None and row.year_to is None:
        return "in"
    if year is None:
        return "unknown"
    low = row.year_from if row.year_from is not None else year
    high = row.year_to if row.year_to is not None else year
    return "in" if low <= year <= high else "out"


def _specific_answer(rows: Sequence[Row], profile: Profile) -> str:
    full_include = partial = partial_exclusion = False
    for row in rows:
        year = _year_state(row, profile.year)
        if _covers_node(row, profile):
            if row.is_exclusion:
                if year == "in":
                    return NO  # an exclusion beats an include at the same or an ancestor depth
                partial_exclusion |= year == "unknown"
            elif year == "in":
                full_include = True
            else:
                partial |= year == "unknown"
        elif _below_node(row, profile) and year != "out":
            if row.is_exclusion:
                partial_exclusion = True
            else:
                partial = True
    if full_include:
        return CHECK if partial_exclusion else FITS
    return CHECK if partial else NO


def answer(mode: str, rows: Sequence[Row], profile: Profile) -> str:
    """`fits`, `check`, `no` or `unknown` for a profile against a product's declaration."""
    if mode == UNIVERSAL:
        return FITS
    if mode == SPECIFIC:
        return _specific_answer(rows, profile)
    return UNKNOWN


def fits(mode: str, rows: Sequence[Row], profile: Profile) -> bool:
    return answer(mode, rows, profile) == FITS


def check(mode: str, rows: Sequence[Row], profile: Profile) -> bool:
    return answer(mode, rows, profile) == CHECK


def no(mode: str, rows: Sequence[Row], profile: Profile) -> bool:
    return answer(mode, rows, profile) == NO


def unknown(mode: str, rows: Sequence[Row], profile: Profile) -> bool:
    return answer(mode, rows, profile) == UNKNOWN


# --- Validation of a declaration (spec 17 §17.2.4)


@dataclass
class Problems:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _bounds(row: Row, low: int, high: int) -> tuple[int, int]:
    return (
        row.year_from if row.year_from is not None else low,
        row.year_to if row.year_to is not None else high,
    )


def _years_overlap(first: Row, second: Row) -> bool:
    # Open ends are compared on a range wider than any year.
    a_low, a_high = _bounds(first, -(10**6), 10**6)
    b_low, b_high = _bounds(second, -(10**6), 10**6)
    return a_low <= b_high and b_low <= a_high


def uncovered_exclusions(rows: Sequence[Row]) -> list[Row]:
    """The exclusions with no include row above them (or at the same node) in overlapping years."""
    return [
        row
        for row in rows
        if row.is_exclusion
        and not any(
            not other.is_exclusion
            and row.node_path.startswith(other.node_path)
            and _years_overlap(row, other)
            for other in rows
        )
    ]


def declaration_problems(
    mode: str,
    rows: Sequence[Row],
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    year_min: int | None = None,
    year_max: int | None = None,
    years_enabled: bool = True,
) -> Problems:
    """The errors that refuse a declaration and the warnings that only inform the seller."""
    problems = Problems()
    errors = problems.errors
    if mode not in MODES:
        errors.append(f"Unknown declaration {mode!r}.")
        return problems
    includes = [row for row in rows if not row.is_exclusion]
    if mode == SPECIFIC and not includes:
        errors.append("Specific items need at least one row that fits.")
    if mode == UNIVERSAL and includes:
        errors.append("An item that fits everything takes no row that fits; remove the rows.")
    if mode == UNDECLARED and rows:
        errors.append("An undeclared item takes no rows.")
    if len(rows) > max_rows:
        errors.append(
            f"A product has at most {max_rows} rows: use a higher-level node for a bigger set."
        )
    for number, row in enumerate(rows, start=1):
        has_years = row.year_from is not None or row.year_to is not None
        if has_years and not years_enabled:
            errors.append(f"Row {number}: this scheme does not use a year.")
            continue
        if row.year_from is not None and row.year_to is not None and row.year_from > row.year_to:
            errors.append(f"Row {number}: the first year is after the last year.")
        for value in (row.year_from, row.year_to):
            if value is None:
                continue
            if (year_min is not None and value < year_min) or (
                year_max is not None and value > year_max
            ):
                errors.append(
                    f"Row {number}: the year {value} is outside {year_min} to {year_max}."
                )
    for i, first in enumerate(rows):
        for j in range(i + 1, len(rows)):
            second = rows[j]
            if first.node_path != second.node_path:
                continue
            if first.is_exclusion == second.is_exclusion:
                if (first.year_from, first.year_to) == (second.year_from, second.year_to):
                    errors.append(f"Row {j + 1} repeats row {i + 1}.")
            elif _years_overlap(first, second):
                errors.append(
                    f"Rows {i + 1} and {j + 1} contradict each other: the same item is both "
                    "included and excluded in overlapping years."
                )
    if not errors and mode != UNIVERSAL:
        for number, row in enumerate(rows, start=1):
            if row in uncovered_exclusions(rows):
                problems.warnings.append(
                    f"Row {number}: an exclusion without anything to exclude from."
                )
    return problems


# --- Account garage profiles (FIT.5, spec 17 §17.4.1)

MAX_PROFILE_NAME = 40
DEFAULT_MAX_PROFILES = 10


def profile_name_error(name: str) -> str | None:
    cleaned = clean_name(name)
    if not cleaned:
        return "A profile needs a name."
    if len(cleaned) > MAX_PROFILE_NAME:
        return f"A profile name has at most {MAX_PROFILE_NAME} characters."
    return None


def default_profile_name(labels: Sequence[str], year: int | None) -> str:
    """ "<make> <model> <year>": the node names from the top down, then the year, cut to fit."""
    parts = [*labels, *([str(year)] if year is not None else [])]
    return clean_name(" ".join(parts))[:MAX_PROFILE_NAME].strip()


def copy_name(name: str, taken: Sequence[str]) -> str:
    """ "Car (copy)", then "Car (copy 2)", ...: the first name not in `taken` (compared folded)."""
    used = {normalize_name(other) for other in taken}
    number = 1
    while True:
        suffix = " (copy)" if number == 1 else f" (copy {number})"
        candidate = name[: MAX_PROFILE_NAME - len(suffix)].rstrip() + suffix
        if normalize_name(candidate) not in used:
            return candidate
        number += 1


def profile_limit_error(count: int, maximum: int) -> str | None:
    if count >= maximum:
        return f"A garage holds at most {maximum} profiles: delete one first."
    return None


def profile_year_error(year: int | None, *, enabled: bool, low: int, high: int) -> str | None:
    if year is None:
        return None
    if not enabled:
        return "This scheme does not use a year."
    if not low <= year <= high:
        return f"The year must be between {low} and {high}."
    return None


def profile_node_retired(status: str) -> bool:
    """A retired node stays in profiles, shown with a note; matching treats it as before."""
    return status == RETIRED


# --- 24-month inactivity cleanup (FIT.5 3/3, spec 17 §17.4.5) ----------------------------------

# Defaults of the scheme settings `garage_inactivity_months` and `garage_notice_days`.
INACTIVITY_MONTHS = 24
NOTICE_DAYS = 30
KEEP = "keep"
NOTIFY = "notify"
DELETE = "delete"


def add_months(moment: dt.datetime, months: int) -> dt.datetime:
    """`moment` moved forward by whole calendar months; the day is clamped to the month's end."""
    index = moment.year * 12 + moment.month - 1 + months
    year, month = divmod(index, 12)
    day = min(moment.day, calendar.monthrange(year, month + 1)[1])
    return moment.replace(year=year, month=month + 1, day=day)


def cleanup_action(
    last_used_at: dt.datetime,
    notice_sent_at: dt.datetime | None,
    now: dt.datetime,
    *,
    months: int = INACTIVITY_MONTHS,
    notice_days: int = NOTICE_DAYS,
) -> str:
    """What the nightly cleanup does with a profile: `KEEP`, `NOTIFY` (send the service email)
    or `DELETE`. The notice goes out `notice_days` before the limit (last use plus `months`);
    deletion needs the limit reached and the full notice period run since the notice, so a late
    notice still gives the customer the whole period. A notice sent before the last use is void
    (the customer came back)."""
    limit = add_months(last_used_at, months)
    period = dt.timedelta(days=notice_days)
    if notice_sent_at is not None and notice_sent_at >= last_used_at:
        return DELETE if now >= limit and now >= notice_sent_at + period else KEEP
    return NOTIFY if now >= limit - period else KEEP
