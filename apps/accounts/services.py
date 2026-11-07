"""Account services: business logic shared by views, API and commands."""

from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages

from .models import User


def set_preferred_language(user: User, language: str, *, dry_run: bool = False) -> ChangeSet:
    """Remember the user's language for emails and documents (spec 09 §9.2).

    Not a sensitive mutation, so no AuditLog entry.
    """
    if language not in configured_languages():
        raise ValueError(f"Language {language!r} is not configured in settings.LANGUAGES")
    changeset = ChangeSet()
    changeset.update(
        "accounts.user",
        str(user.public_id),
        {"preferred_language": user.preferred_language},
        {"preferred_language": language},
    )
    if not dry_run and not changeset.is_empty:
        user.preferred_language = language
        user.save(update_fields=["preferred_language", "updated_at"])
    return changeset
