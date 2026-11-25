"""System checks for account security settings: captcha keys (CORE.5) and the MFA middleware
stack (CORE.4)."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error
from django.utils.module_loading import import_string


def check_captcha_settings(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    """An enabled captcha without keys would lock everybody out of every login form."""
    if not settings.CAPTCHA_ENABLED:
        return []
    missing = [
        name
        for name in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY")
        if not getattr(settings, name)
    ]
    if not missing:
        return []
    return [
        Error(
            f"CAPTCHA_ENABLED is true but {' and '.join(missing)} is empty.",
            hint="Set the Turnstile keys in the environment, or set MPMV_CAPTCHA_ENABLED=false.",
            id="accounts.E001",
        )
    ]


def check_breach_check_url(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """The real breach client must call an https URL (CORE.28); a fake checker has no URL."""
    if not settings.BREACH_CHECK_ENABLED:
        return []
    from .breach import HibpRangeChecker

    if import_string(settings.BREACH_CHECKER) is not HibpRangeChecker:
        return []
    if str(settings.BREACH_CHECK_URL).startswith("https://"):
        return []
    return [
        Error(
            "BREACH_CHECK_URL must start with https://.",
            hint="Set MPMV_BREACH_CHECK_URL to an https URL, or unset it for the default.",
            id="accounts.E002",
        )
    ]


AUTHENTICATION = "django.contrib.auth.middleware.AuthenticationMiddleware"
OTP = "django_otp.middleware.OTPMiddleware"
MFA = "apps.accounts.middleware.MfaRequiredMiddleware"


def check_mfa_middleware(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    middleware = list(settings.MIDDLEWARE)
    required = [AUTHENTICATION, OTP, MFA]
    if all(m in middleware for m in required) and (
        middleware.index(AUTHENTICATION) < middleware.index(OTP) < middleware.index(MFA)
    ):
        return []
    return [
        Error(
            "MFA enforcement is not installed: MIDDLEWARE must list "
            f"{AUTHENTICATION}, {OTP} and {MFA} in this order.",
            hint="Without it, staff could use the site and the admin without a second factor.",
            id="accounts.E002",
        )
    ]


def check_shop_owner_check(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """`SHOP_OWNER_CHECK` decides who may confirm a shop action; a path that cannot be imported
    would silently deny every vendor owner (the check fails closed), so catch it at startup."""
    try:
        import_string(settings.SHOP_OWNER_CHECK)
    except (ImportError, AttributeError):
        return [
            Error(
                "SHOP_OWNER_CHECK cannot be imported.",
                hint="Set it to the dotted path of the function (user, shop) -> bool that "
                "says whether the user owns the shop.",
                id="accounts.E003",
            )
        ]
    try:
        import_string(settings.SHOP_ORDERS_CHECK)
    except (ImportError, AttributeError):
        return [
            Error(
                "SHOP_ORDERS_CHECK cannot be imported.",
                hint="Set it to the dotted path of the function (user, shop) -> bool that "
                "says whether the user handles the shop's orders.",
                id="accounts.E004",
            )
        ]
    return []
