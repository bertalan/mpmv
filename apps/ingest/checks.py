"""System checks of the ingest app (ING.5)."""

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error

ENRICHERS = ("fake", "gemini")


def check_enricher_settings(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """A selected provider needs its key (ingest.E001) and a known name (ingest.E002): without the
    check a missing key is only found when the first enrichment job runs."""
    choice = settings.INGEST_ENRICHER
    if choice not in ENRICHERS:
        return [
            Error(
                f"MPMV_INGEST_ENRICHER {choice!r} is not one of {', '.join(ENRICHERS)}.",
                hint="Use fake (the default) or gemini.",
                id="ingest.E002",
            )
        ]
    if choice == "gemini" and not settings.INGEST_GEMINI_API_KEY:
        return [
            Error(
                "MPMV_INGEST_ENRICHER=gemini needs MPMV_INGEST_GEMINI_API_KEY.",
                hint="Set the key in the environment, or switch back to fake.",
                id="ingest.E001",
            )
        ]
    return []
