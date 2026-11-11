"""System checks of the consent provider (SEO.9).

- seo.E001: `MPMV_CONSENT_PROVIDER` names no registered provider, or its class cannot be loaded.
- seo.W001: the provider needs a script origin that `SECURE_CSP` does not allow.
"""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from .providers import get_provider


def check_consent_provider(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    try:
        provider = get_provider()
    except ImproperlyConfigured as exc:
        return [
            checks.Error(
                str(exc),
                hint=(
                    "Use a name from MPMV_CONSENT_PROVIDERS or 'self-hosted' "
                    "(docs/dev/consent-providers.md)."
                ),
                id="seo.E001",
            )
        ]
    allowed = {str(s) for s in (getattr(settings, "SECURE_CSP", {}) or {}).get("script-src", [])}
    missing = [origin for origin in provider.csp_origins() if origin not in allowed]
    if missing:
        return [
            checks.Warning(
                f"Consent provider {provider.name!r} needs {', '.join(missing)} in the "
                "script-src of SECURE_CSP.",
                hint="Add the origin by its own reviewed change (spec 14 section 14.5).",
                id="seo.W001",
            )
        ]
    return []
