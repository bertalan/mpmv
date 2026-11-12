"""On-demand account activation routes (CORE.19), mounted under /account/activate/."""

from django.urls import path

from . import activation_views

app_name = "activation"

urlpatterns = [
    path("", activation_views.request_link, name="request"),
    path("sent/", activation_views.sent, name="sent"),
    path("set/<str:user_key>/", activation_views.set_password, name="set_password"),
]
