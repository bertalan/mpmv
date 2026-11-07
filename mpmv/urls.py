"""Root URLconf.

Operational routes (health check, staff admin, language switcher, API v1, MFA pages) have no
language prefix.
Public pages live in `i18n_patterns`: `/` is the primary language (EN), every other language in
settings.LANGUAGES gets `/<code>/` (spec 09 §9.2, I18N.1).
"""

from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path

from apps.accounts.views import switch_language
from apps.core.views import home


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for the container runtime and uptime checks."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("django-admin/", admin.site.urls),
    path("i18n/switch/", switch_language, name="switch_language"),
    path("api/v1/", include("apps.api.urls")),
    path("account/mfa/", include("apps.accounts.urls")),
    *i18n_patterns(
        path("", home, name="home"),
        prefix_default_language=False,
    ),
]
