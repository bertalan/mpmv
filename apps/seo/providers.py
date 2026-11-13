"""Consent providers (SEO.9, owner decision D-10).

The consent banner is one provider behind `ConsentProvider`. The default, `self-hosted`, is our
own minimal banner (SEO.5). An operator who prefers an external certified CMP writes a provider
class and selects it with settings, no fork: see docs/dev/consent-providers.md.

    MPMV_CONSENT_PROVIDER = "self-hosted"      # the name of the provider in use
    MPMV_CONSENT_PROVIDERS = {"acme": "acme_cmp.provider.AcmeProvider"}   # extra names

An unknown name fails the `seo.E001` system check.
"""

import abc
import json
import re
import uuid
from collections.abc import Iterable
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.module_loading import import_string
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from . import engine, services
from .engine import ConsentState

DEFAULT_PROVIDER = "self-hosted"
BUILTIN_PROVIDERS = {DEFAULT_PROVIDER: "apps.seo.providers.SelfHostedConsentProvider"}

_SAFE_SRC = re.compile(r"^(https://[A-Za-z0-9.-]+(:\d+)?(/[^\s\"'<>]*)?|/(?!/)[^\s\"'<>]*)$")


class ConsentProvider(abc.ABC):
    """What the platform needs from a consent tool. Tag-gating code only calls these methods."""

    name: ClassVar[str]
    # True when the provider's choices arrive at our `consent/` endpoint (the self-hosted one).
    accepts_choices: ClassVar[bool] = False

    @abc.abstractmethod
    def state(self, request: HttpRequest) -> ConsentState:
        """The visitor's choice, for server-side decisions. Undecided until a choice is made."""

    def allows(self, request: HttpRequest, category: str) -> bool:
        """Whether `category` may run for this request. `necessary` is always allowed."""
        return self.state(request).allows(category)

    def signals(self, request: HttpRequest) -> dict[str, str]:
        """Consent Mode v2 signals (`granted` or `denied`) that follow the choice."""
        return engine.consent_signals(self.state(request))

    @abc.abstractmethod
    def render_banner(self, request: HttpRequest | None) -> str:
        """The banner markup for every page, the same for every visitor (the page may be
        cached): the browser decides whether to show it. May be empty for a tool that injects
        its own banner."""

    @abc.abstractmethod
    def gate_script(self, category: str, src: str) -> str:
        """Markup of a script that must not run before `category` is granted: it must not load
        `src` until then. Raises `ValueError` for an unknown category or an unsafe `src`."""

    @abc.abstractmethod
    def record(
        self, request: HttpRequest, action: str, selected: Iterable[str] = ()
    ) -> ConsentState:
        """Keep the proof of a choice (or change of choice) and return the new state."""

    def preferences_url(self) -> str | None:
        """Where a visitor changes the choice later; None when the tool offers no such page."""
        return None

    def csp_origins(self) -> tuple[str, ...]:
        """Origins the provider needs in `script-src` (`seo.W001` warns when one is missing)."""
        return ()


class SelfHostedConsentProvider(ConsentProvider):
    """Our own minimal banner: accept and reject with equal weight, categories, a consent log."""

    name = DEFAULT_PROVIDER
    accepts_choices = True

    def state(self, request: HttpRequest) -> ConsentState:
        return engine.parse_cookie(request.COOKIES.get(engine.COOKIE_NAME))

    def render_banner(self, request: HttpRequest | None) -> str:
        return render_to_string(
            "seo/consent/banner.html",
            {
                "categories": category_texts(),
                "action_url": reverse("consent_save"),
                "preferences_url": reverse("consent_preferences"),
                "banner_version": engine.BANNER_VERSION,
                "cookie_name": engine.COOKIE_NAME,
                "signal_map": json.dumps(engine.SIGNAL_CATEGORY),
            },
        )

    def gate_script(self, category: str, src: str) -> str:
        if category not in engine.GATED_CATEGORIES:
            raise ValueError(f"{category!r} is not a category that needs consent")
        if not _SAFE_SRC.match(src):
            raise ValueError("A gated script needs an https:// or same-site path source")
        # Inert: text/plain never runs and `src` is only in `data-src`; the page script starts it
        # once the category is granted.
        return format_html(
            '<script type="text/plain" data-consent="{}" data-src="{}"></script>', category, src
        )

    def record(
        self, request: HttpRequest, action: str, selected: Iterable[str] = ()
    ) -> ConsentState:
        previous = self.state(request)
        granted = engine.granted_for(action, selected)
        state = engine.ConsentState(
            True, granted, engine.BANNER_VERSION, previous.consent_id or uuid.uuid4().hex
        )
        services.record_consent(
            consent_id=state.consent_id,
            action=action,
            granted=granted,
            banner_version=state.banner_version,
            provider=self.name,
            language=get_language() or "",
        )
        return state

    def preferences_url(self) -> str | None:
        return reverse("consent_preferences")


def category_texts() -> list[dict[str, object]]:
    """The categories as the banner and the preferences page show them."""
    return [
        {
            "code": engine.NECESSARY,
            "label": _("Necessary"),
            "help": _("Keeps the site working: sign-in, cart and security. Always on."),
            "locked": True,
        },
        {
            "code": engine.PREFERENCES,
            "label": _("Preferences"),
            "help": _("Remembers choices you make, such as your garage or your layout."),
            "locked": False,
        },
        {
            "code": engine.STATISTICS,
            "label": _("Statistics"),
            "help": _("Shows us, in aggregate, which pages are used so we can improve the shop."),
            "locked": False,
        },
        {
            "code": engine.MARKETING,
            "label": _("Marketing"),
            "help": _("Lets us measure our adverts and show you offers on other sites."),
            "locked": False,
        },
    ]


def registered_providers() -> dict[str, str]:
    """Provider name to dotted class path: the built-in one plus `MPMV_CONSENT_PROVIDERS`."""
    return {**BUILTIN_PROVIDERS, **getattr(settings, "MPMV_CONSENT_PROVIDERS", {})}


def provider_name() -> str:
    return str(getattr(settings, "MPMV_CONSENT_PROVIDER", DEFAULT_PROVIDER))


def get_provider() -> ConsentProvider:
    """The provider `MPMV_CONSENT_PROVIDER` names; `ImproperlyConfigured` when it names none."""
    name = provider_name()
    path = registered_providers().get(name)
    if path is None:
        known = ", ".join(sorted(registered_providers()))
        raise ImproperlyConfigured(
            f"MPMV_CONSENT_PROVIDER is {name!r}, which is not a registered consent provider "
            f"(known: {known})."
        )
    try:
        provider_class = import_string(path)
    except ImportError as exc:
        raise ImproperlyConfigured(f"Consent provider {name!r}: cannot import {path}.") from exc
    if not (isinstance(provider_class, type) and issubclass(provider_class, ConsentProvider)):
        raise ImproperlyConfigured(f"Consent provider {name!r}: {path} is not a ConsentProvider.")
    provider: ConsentProvider = provider_class()
    return provider
