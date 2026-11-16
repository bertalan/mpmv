"""The account's sessions (CORE.24), mounted without a language prefix under /account/sessions/."""

from django.urls import path

from . import session_views

app_name = "sessions"

urlpatterns = [
    path("", session_views.sessions, name="list"),
    path("others/end/", session_views.end_others, name="end_others"),
    path("<uuid:public_id>/end/", session_views.end_one, name="end_one"),
]
