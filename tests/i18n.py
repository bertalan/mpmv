"""I18N.15: language helpers for tests, so no test assumes `en` or `it` is configured.

The suite runs with the default languages and also with another `MPMV_LANGUAGES` (CI job
`it,de`). Tests ask for "the primary language" and "a second language" instead of naming them.
"""

import pytest
from django.conf import settings

from apps.core.i18n import configured_languages, primary_language


def other_language() -> str:
    """The first configured language that is not the primary one (`it`, or `de` with it,de)."""
    for code in configured_languages():
        if code != primary_language():
            return code
    pytest.skip("needs a second configured language")


def third_language() -> str:
    """The first configured language that is neither the primary nor `other_language()`."""
    taken = {primary_language(), other_language()}
    for code in configured_languages():
        if code not in taken:
            return code
    pytest.skip("needs a third configured language")


def language_prefix(code: str) -> str:
    """The URL prefix of a language: empty for the primary one, `/<code>` for the others."""
    return "" if code == settings.LANGUAGE_CODE else f"/{code}"
