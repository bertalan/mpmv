"""Sitemap routes (I18N.6): outside the language prefixes, one index and one file per language."""

from django.urls import path

from . import sitemap_views

urlpatterns = [
    path("sitemap.xml", sitemap_views.sitemap_index, name="sitemap"),
    path("sitemap-<slug:language>.xml", sitemap_views.sitemap_language, name="sitemap_language"),
]
