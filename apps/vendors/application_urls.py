"""Public "Sell with us" application routes (VEN.22), without a language prefix under
/vendor/apply/. `vendors:apply` is the URL the CMS recruitment page links to (CMS.5)."""

from django.urls import path

from . import application_views as views

app_name = "vendors"

urlpatterns = [
    path("", views.apply, name="apply"),
    path("sent/", views.sent, name="apply_sent"),
    path("confirm/<str:user_key>/", views.confirm, name="apply_confirm"),
    path("cancel/<str:user_key>/", views.cancel, name="apply_cancel"),
]
