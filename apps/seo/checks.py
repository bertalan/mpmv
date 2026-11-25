"""System checks of the consent provider (SEO.9).

- seo.E001: `MPMV_CONSENT_PROVIDER` names no registered provider, or its class cannot be loaded.
- seo.W001: the provider needs a script origin that `SECURE_CSP` does not allow.
- seo.W002: the platform operator has no logo (SEO.16), so its `Organization` node has none.
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


def check_operator_logo(
    app_configs: Iterable[AppConfig] | None, databases: Iterable[str] | None = None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """SEO.16, a database check (it runs with `migrate` and `check --database`): Google wants a
    logo on the site's `Organization`; without one the rich result has no logo."""
    if not databases:
        return []
    from django.db import DatabaseError

    from apps.core.services import NoPlatformOperator, current_operator

    try:
        operator = current_operator()
    except (NoPlatformOperator, DatabaseError):  # a fresh install or no tables yet
        return []
    if operator.logo_id is not None:
        return []
    return [
        checks.Warning(
            "The platform operator has no logo, so search engines get none for the site.",
            hint="Pick one in the admin: Platform operator > Logo (from 'Logos and icons').",
            id="seo.W002",
        )
    ]
