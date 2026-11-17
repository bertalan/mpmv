"""EVT.6: the interface strings of the public event pages are translated in the IT, DE, FR and
ES catalogs (the compiled catalogs match their sources: `make i18n-check`)."""

from pathlib import Path

import polib
import pytest

LOCALE = Path(__file__).resolve().parents[4] / "locale"
STRINGS = (
    "Add to calendar",
    "Report this event",
    "Filter events",
    "All vendors",
    "Month calendar",
    "Past events",
    "Cancelled",
    "Trade fair or meeting",
    "No events are planned at the moment.",
)


@pytest.mark.parametrize("language", ["it", "de", "fr", "es"])
def test_the_event_page_strings_are_translated(language: str) -> None:
    catalog = polib.pofile(str(LOCALE / language / "LC_MESSAGES" / "django.po"))
    translated = {entry.msgid: entry.msgstr for entry in catalog if entry.msgstr}
    for text in STRINGS:
        assert translated.get(text), (language, text)
    plural = next(e for e in catalog if e.msgid == "%(counter)s event")
    assert all(plural.msgstr_plural.values())
