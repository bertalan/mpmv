"""Pure rules for changing the site languages (I18N.15, spec 09). No ORM, no Django.

Adding a language is a setting. Removing one, or changing the primary one (the first), moves
URLs: the command built on this module records the new set and redirects the old prefixes.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from mpmv.settings.languages import KNOWN_LANGUAGES


class LanguageChangeError(ValueError):
    """The requested language list is not acceptable."""


def url_prefix(code: str, primary: str) -> str:
    """`/` for the primary language, `/<code>/` for the others."""
    return "/" if code == primary else f"/{code}/"


@dataclass(frozen=True)
class LanguagePlan:
    old: tuple[str, ...]
    new: tuple[str, ...]
    removed: tuple[str, ...]
    added: tuple[str, ...]
    primary_changed: bool
    redirects: tuple[tuple[str, str], ...]  # (old prefix, new prefix)
    notes: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return self.old == self.new


def plan_language_change(current: Sequence[str], target: Sequence[str]) -> LanguagePlan:
    """What changes going from `current` to `target` (both ordered, first = primary)."""
    old, new = tuple(current), tuple(target)
    if not new:
        raise LanguageChangeError("At least one language is required.")
    if len(set(new)) != len(new):
        raise LanguageChangeError("A language is listed twice.")
    unknown = [code for code in new if code not in KNOWN_LANGUAGES]
    if unknown:
        raise LanguageChangeError(f"Unknown language code(s): {', '.join(unknown)}.")
    old_primary = old[0] if old else new[0]
    new_primary = new[0]
    removed = tuple(code for code in old if code not in new)
    added = tuple(code for code in new if code not in old)
    primary_changed = bool(old) and old_primary != new_primary
    redirects: list[tuple[str, str]] = []
    notes: list[str] = []
    # A removed language had a prefix unless it was the primary one: its pages go to the new
    # primary language.
    for code in removed:
        if code != old_primary:
            redirects.append((url_prefix(code, old_primary), "/"))
    if primary_changed and new_primary in old and new_primary not in removed:
        # The new primary language loses its prefix.
        redirects.append((url_prefix(new_primary, old_primary), "/"))
    if primary_changed:
        notes.append(
            f"The unprefixed URLs served {old_primary!r} and now serve {new_primary!r}: they "
            "cannot be redirected. "
            + (
                f"The old primary language is now at /{old_primary}/."
                if old_primary in new
                else f"{old_primary!r} is removed."
            )
        )
    return LanguagePlan(
        old, new, removed, added, primary_changed, tuple(dict.fromkeys(redirects)), tuple(notes)
    )


def settings_conflict(
    recorded: Sequence[str], configured: Sequence[str], removed: Sequence[str] = ()
) -> str | None:
    """Why `configured` (the settings) may not replace `recorded` (applied by the command).

    Only additions are a settings matter; a removed language or a new primary is not, and a
    language the command removed (`removed`) stays out until the command brings it back.
    """
    if not recorded or not configured:
        return None
    message = None
    dropped = [code for code in recorded if code not in configured]
    revived = [code for code in configured if code in removed and code not in recorded]
    if dropped:
        message = f"MPMV_LANGUAGES drops {', '.join(dropped)}, which the site still serves."
    elif revived:
        message = (
            f"MPMV_LANGUAGES lists {', '.join(revived)}, which change_languages removed: "
            "drop it from the setting, or bring it back with the command."
        )
    elif configured[0] != recorded[0]:
        message = (
            f"MPMV_LANGUAGES changes the primary language from {recorded[0]!r} to "
            f"{configured[0]!r}."
        )
    return message
