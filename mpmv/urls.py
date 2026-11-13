"""Root URLconf.

Operational routes (health check, email provider webhook, image files, staff admin, editors'
admin, language switcher, API v1, MFA pages, the Manager and its sign-in) have no language prefix.
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
from django.views.decorators.http import require_safe
from wagtail.admin import urls as wagtailadmin_urls

from apps.accounts.views import switch_language
from apps.core.media import serve_media
from apps.core.views import email_webhook, robots_view

# Error pages with the `base` layouts (THM.3); 400 and 500 render without the database.
handler400 = "apps.themes.views.bad_request"
handler500 = "apps.themes.views.server_error"


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for the container runtime and uptime checks."""
    return JsonResponse({"status": "ok"})


@require_safe
def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness probe (OPS.2): database, cache and storage answer. Status only, no detail."""
    from apps.core.health import readiness_failures

    ok = not readiness_failures()
    response = JsonResponse({"status": "ok" if ok else "unavailable"}, status=200 if ok else 503)
    response["Cache-Control"] = "no-store"
    return response


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("robots.txt", robots_view, name="robots_txt"),  # SEO.8
    path("webhooks/email/", email_webhook, name="email_webhook"),
    path("webhooks/payments/", include("apps.payments.urls")),  # PAY.2
    # Uploaded images and renditions, as images only (THM.12, apps.core.media).
    path("media/<path:path>", serve_media, name="media"),
    path("django-admin/", admin.site.urls),
    path("admin/", include("apps.cms.admin_urls")),  # replaces Wagtail's account page (CMS.1)
    path("admin/", include("apps.core.admin_urls")),  # collection moves keep SVG (THM.12)
    path("admin/", include(wagtailadmin_urls)),  # editors' admin (Wagtail, CMS.1)
    path("i18n/switch/", switch_language, name="switch_language"),
    path("api/v1/", include("apps.api.urls")),
    path("account/mfa/", include("apps.accounts.urls")),
    path("account/level/", include("apps.accounts.level_urls")),  # CORE.25 (D-32)
    path("account/link/", include("apps.accounts.action_link_urls")),  # CORE.15
    path("account/privacy/", include("apps.compliance.account_urls")),  # CMP.4 self-service
    path("account/orders/", include("apps.orders.account_urls")),  # My orders (ORD.7)
    path("account/", include("apps.manager.account_urls")),  # Manager sign-in (CORE.17)
    path("manager/", include("apps.manager.urls")),  # the Manager's two spaces (CORE.17)
    path("vendor/claim/", include("apps.vendors.urls")),  # claim an imported vendor (VEN.24)
    path("_help/", include("apps.cms.help_urls")),  # contextual help dialog (ARC.32)
    path("geocoding/", include("apps.core.geocoding.urls")),  # "Find coordinates" (EVT.12)
    # Staff-only styleguide of the active theme (THM.4); a string, as no app imports themes.
    path("_styleguide/", include("apps.themes.urls")),
    # Temporary staff "Site links" page for STAGE testing (OPS.12); remove before LIVE.
    path("_links/", include("apps.seo.urls")),
    *i18n_patterns(
        path("vendor/apply/", include("apps.vendors.application_urls")),  # "Sell with us" (VEN.22)
        path("", include("apps.orders.ui.urls")),  # cart and checkout (ORD.5)
        path("", include("apps.catalog.urls")),  # listings (CAT.8): before the CMS catch-all
        path("", include("apps.compliance.urls")),  # DSA contact points (CMP.5)
        path("", include("apps.fitment.urls")),  # anonymous garage cookie (FIT.6)
        path("", include("apps.cms.urls")),
        prefix_default_language=False,
    ),
]
