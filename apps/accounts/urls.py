"""MFA routes (CORE.4), mounted without a language prefix under /account/mfa/.

Every MFA page is `never_cache` (SEC.5): they show TOTP secrets, recovery codes and the user's
factors, which no browser, proxy or CDN cache may keep.
"""

from django.urls import URLPattern, path
from django.views.decorators.cache import never_cache

from . import mfa_views

app_name = "mfa"

_routes: list[URLPattern] = [
    path("", mfa_views.overview, name="overview"),
    path("setup/", mfa_views.setup, name="setup"),
    path("totp/", mfa_views.totp, name="totp"),
    path("verify/", mfa_views.verify, name="verify"),
    path("recovery-codes/", mfa_views.recovery_codes, name="recovery_codes"),
    path(
        "passkey/register/options/",
        mfa_views.passkey_register_options,
        name="passkey_register_options",
    ),
    path("passkey/register/", mfa_views.passkey_register, name="passkey_register"),
    path(
        "passkey/authenticate/options/",
        mfa_views.passkey_auth_options,
        name="passkey_auth_options",
    ),
    path("passkey/authenticate/", mfa_views.passkey_auth, name="passkey_auth"),
    path(
        "factors/<str:kind>/<str:public_id>/remove/",
        mfa_views.remove_device,
        name="remove_device",
    ),
]

urlpatterns = [
    path(str(route.pattern), never_cache(route.callback), name=route.name) for route in _routes
]
