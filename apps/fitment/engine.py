"""Pure rules of the fitment scheme and its nodes (D-38, spec 17 §17.2). No Django, no database.

Names are compared after NFC, case and punctuation folding, so "Model-X" and "model x" are one
name. A scheme has one to four node levels, in a fixed order; nodes sit at the depth of their
level. The matching of products to profiles (FIT.2) will live here too.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence

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
