"""Provider registry (PAY.5): the provider is chosen by `settings.PAYMENT_PROVIDER`.

Registration runs `capability_problems`, so a provider missing a capability is refused when it
is registered, not when a customer pays. `apps.payments.checks` (payments.E001, payments.E002)
refuses an unknown or unusable configured provider at `manage.py check`.
"""

from collections.abc import Callable

from django.conf import settings

from .engine import PaymentProvider, capability_problems
from .fake import FakePaymentProvider

ProviderFactory = Callable[[], PaymentProvider]

_FACTORIES: dict[str, ProviderFactory] = {}
_INSTANCES: dict[str, PaymentProvider] = {}


class ProviderRefused(Exception):
    """The provider lacks a capability or breaks a rule (D-08), so it cannot be registered."""


class UnknownProvider(Exception):
    pass


def register_provider(factory: ProviderFactory) -> None:
    provider = factory()
    problems = capability_problems(provider)
    if problems:
        raise ProviderRefused(f"{type(provider).__name__}: " + "; ".join(problems))
    if provider.code in _FACTORIES:
        raise ProviderRefused(f"provider code {provider.code!r} is already registered")
    _FACTORIES[provider.code] = factory
    _INSTANCES[provider.code] = provider


def unregister_provider(code: str) -> None:
    _FACTORIES.pop(code, None)
    _INSTANCES.pop(code, None)


def registered_codes() -> list[str]:
    codes = set(_FACTORIES)
    if getattr(settings, "PAYMENT_ALLOW_FAKE_PROVIDER", False):
        codes.add(FakePaymentProvider.code)
    return sorted(codes)


def get_provider(code: str) -> PaymentProvider:
    """The provider instance for `code`; the fake one exists only where settings allow it."""
    if code not in registered_codes():
        raise UnknownProvider(code)
    if code not in _INSTANCES:
        _INSTANCES[code] = _FACTORIES[code]() if code in _FACTORIES else FakePaymentProvider()
    return _INSTANCES[code]


def configured_provider() -> PaymentProvider | None:
    """The provider named by `PAYMENT_PROVIDER`, or None while none is configured."""
    code = settings.PAYMENT_PROVIDER
    return get_provider(code) if code else None
