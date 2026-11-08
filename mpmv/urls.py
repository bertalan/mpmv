"""Root URLconf.

Operational routes (health check, email provider webhook, staff admin, editors' admin, language
switcher, API v1, MFA pages) have no language prefix.
Public pages live in `i18n_patterns`: `/` is the primary language (EN), every other language in
settings.LANGUAGES gets `/<code>/` (spec 09 §9.2, I18N.1). The catalog listings come first
(CAT.8: category, `/{taxonomy}/{term}/`, brand and vendor storefront pages); Wagtail serves the
rest from the page tree (CMS.1), so the CMS routes come last: a path no other route matches is a
page lookup.
"""

from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path
from wagtail.admin import urls as wagtailadmin_urls

from apps.accounts.views import switch_language
from apps.core.views import email_webhook

# Error pages with the `base` layouts (THM.3); 400 and 500 render without the database.
handler400 = "apps.themes.views.bad_request"
handler500 = "apps.themes.views.server_error"


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for the container runtime and uptime checks."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("webhooks/email/", email_webhook, name="email_webhook"),
    path("django-admin/", admin.site.urls),
    path("admin/", include("apps.cms.admin_urls")),  # replaces Wagtail's account page (CMS.1)
    path("admin/", include(wagtailadmin_urls)),  # editors' admin (Wagtail, CMS.1)
    path("i18n/switch/", switch_language, name="switch_language"),
    path("api/v1/", include("apps.api.urls")),
    path("account/mfa/", include("apps.accounts.urls")),
    path("geocoding/", include("apps.core.geocoding.urls")),  # "Find coordinates" (EVT.12)
    *i18n_patterns(
        path("", include("apps.catalog.urls")),  # listings (CAT.8): before the CMS catch-all
        path("", include("apps.cms.urls")),
        prefix_default_language=False,
    ),
]
