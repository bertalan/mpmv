"""Test-only URLconf for the information-leak scenarios: the real routes plus views that fail.

The handlers are the real ones (mpmv.urls), so the 500 page is the one production renders.
"""

from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.urls import path

from mpmv.urls import handler400, handler500
from mpmv.urls import urlpatterns as site_urlpatterns

__all__ = ["handler400", "handler500", "urlpatterns"]

# Text an exception carries that must never reach a visitor (internals, SQL, a path).
INTERNAL_DETAIL = "internal-detail-7f3a /srv/app/apps/orders/services.py SELECT secret_column"


def boom(request: HttpRequest) -> HttpResponse:
    raise RuntimeError(INTERNAL_DETAIL)


def database_error(request: HttpRequest) -> HttpResponse:
    with connection.cursor() as cursor:
        cursor.execute("SELECT secret_column FROM no_such_table_7f3a")
    return HttpResponse()


urlpatterns = [
    path("leak-test/boom/", boom),
    path("leak-test/database/", database_error),
    *site_urlpatterns,
]
