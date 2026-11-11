"""Customer privacy routes, mounted at `/account/privacy/` without a language prefix (CMP.4)."""

from django.urls import path

from . import privacy_views as views

urlpatterns = [
    path("", views.account, name="privacy_account"),
    path("export/", views.export, name="privacy_export"),
    path("erase/", views.erase, name="privacy_erase"),
    path("erased/", views.erase_done, name="privacy_erase_done"),
    path("requests/", views.requests_list, name="privacy_requests"),
    path(
        "requests/<uuid:public_id>/process/", views.request_process, name="privacy_request_process"
    ),
]
