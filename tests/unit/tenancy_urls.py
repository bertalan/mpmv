"""URLconf for the unit tests of the cross-tenant harness (CORE.3): shapes, not behaviour."""

from django.http import HttpRequest, HttpResponse
from django.urls import include, path, re_path


def view(request: HttpRequest, **kwargs: object) -> HttpResponse:
    return HttpResponse("ok")


nested = (
    [
        path("items/<uuid:public_id>/", view, name="item"),
        re_path(r"^pages/(?P<slug>[a-z-]+)/$", view, name="page"),
    ],
    "shop",
)

urlpatterns = [
    path("", view, name="root"),
    path("orders/<int:pk>/lines/<str:line>/", view, name="order-line"),
    path("unnamed/<path:rest>", view),
    path("shop/", include(nested, namespace="shop")),
]
