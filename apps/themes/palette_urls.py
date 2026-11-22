"""The site palette stylesheet (THM.31, spec 14 §14.12): same-origin, named by its content."""

from django.urls import re_path

from . import views

app_name = "palette"

urlpatterns = [
    re_path(
        r"^(?P<theme>[a-z0-9-]+)/(?P<palette>[a-z0-9_-]+)\.(?P<digest>[0-9a-f]{12})\.css$",
        views.palette_css,
        name="css",
    ),
]
