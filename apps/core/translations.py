"""Pure translation rules shared by every `*Translation` table (spec 09 §9.2, I18N.2).

No ORM here: `apps.core.models.TranslationModel` stores the rows and calls these functions.

- Workflow statuses: `machine` (a draft, also the "needs review" state) → `reviewed` →
  `published`. Only published rows are shown to visitors.
- `source_hash` fingerprints the source-language (EN) texts a translation was made or reviewed
  from. A translation whose hash differs from the current EN hash is stale.
- `resolve` picks the published row for a language, else the published EN row with an explicit
  fallback flag (templates show a notice; sitemaps leave the page out), else nothing.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings

MACHINE = "machine"
REVIEWED = "reviewed"
PUBLISHED = "published"
STATUSES = (MACHINE, REVIEWED, PUBLISHED)


class Translatable(Protocol):
    @property
    def language(self) -> str: ...

    @property
    def status(self) -> str: ...


def source_language() -> str:
    """The primary language every other language is translated from (EN, configuration)."""
    return str(settings.LANGUAGE_CODE)


def source_hash(values: Mapping[str, str]) -> str:
    """SHA-256 of the source texts, keyed by field name; field order does not matter."""
    canonical = json.dumps(dict(values), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Resolved[R]:
    """The translation to show for `requested`, and in which language it actually is."""

    requested: str
    translation: R | None
    language: str | None

    @property
    def is_fallback(self) -> bool:
        """True when the source language is shown instead: templates show a notice."""
        return self.translation is not None and self.language != self.requested

    @property
    def is_missing(self) -> bool:
        return self.translation is None


def resolve[T: Translatable](translations: Iterable[T], language: str) -> Resolved[T]:
    """The published translation in `language`, else the published source one, else none."""
    published = {row.language: row for row in translations if row.status == PUBLISHED}
    for candidate in (language, source_language()):
        if candidate in published:
            return Resolved(language, published[candidate], candidate)
    return Resolved(language, None, None)
