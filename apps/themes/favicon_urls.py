"""`/favicon.ico` (THM.38): answered with the active theme's icon instead of a 404."""

from django.urls import path

from . import views

app_name = "favicon"

urlpatterns = [path("favicon.ico", views.favicon, name="ico")]
