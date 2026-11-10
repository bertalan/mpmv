"""Sign-in and sign-out of the Manager (CORE.17), mounted at `/account/` without a language prefix.

Outside `/manager/`, so every Manager URL stays under a gated space prefix.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
]
