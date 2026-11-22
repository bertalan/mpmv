"""System checks of the vendors app, registered in `VendorsConfig.ready`.

The DSA article 30(1)(e) self-certification texts (CMP.25) are Python literals in `engine`, one
per language, so neither the catalog completeness check (core.W911) nor the catalog review mark
(core.E915, I18N.19) sees them. These two checks hold them to the same rules:

- vendors.E001 (vendors.W001 outside LIVE): a configured language has no self-certification
  text, so an owner using it could not certify and the vendor could not be activated.
- vendors.E002 (vendors.W002 outside LIVE): a configured language has a text that the
  professional translator has not approved (`engine.SELF_CERTIFICATION_REVIEWED_LANGUAGES`).

LIVE is `LIVE_DATA`. STAGE and development get warnings, so staff can preview a language there.
"""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks

from . import engine


def check_self_certification_texts(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """vendors.E001 / W001 (no text) and vendors.E002 / W002 (text not reviewed, I18N.19)."""
    codes = list(dict.fromkeys(str(code) for code, _name in settings.LANGUAGES))
    live = bool(getattr(settings, "LIVE_DATA", False))
    findings = [
        (
            code,
            "001",
            f"Language {code!r} is listed but has no vendor self-certification text (CMP.25): "
            "a vendor owner using it cannot certify.",
            "Add the text to SELF_CERTIFICATION_TEXTS in apps/vendors/engine.py, or remove "
            "the language from MPMV_LANGUAGES.",
        )
        for code in engine.self_certification_languages_missing(codes)
    ] + [
        (
            code,
            "002",
            f"Language {code!r} is listed but its vendor self-certification text is not "
            "marked as reviewed (I18N.19): it is still gated.",
            "Have the professional translator approve the text in apps/vendors/engine.py, "
            "then add the language to SELF_CERTIFICATION_REVIEWED_LANGUAGES; or remove it "
            "from MPMV_LANGUAGES.",
        )
        for code in engine.self_certification_languages_unreviewed(codes)
    ]
    messages: list[checks.CheckMessage] = []
    for _code, number, text, hint in findings:
        if live:
            messages.append(checks.Error(text, hint=hint, id=f"vendors.E{number}"))
        else:
            messages.append(checks.Warning(text, hint=hint, id=f"vendors.W{number}"))
    return messages
