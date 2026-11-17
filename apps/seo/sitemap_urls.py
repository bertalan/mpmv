"""Sitemap and `llms.txt` routes (I18N.6, SEO.2): outside the language prefixes. One index, one
file per language and type (numbered parts when a file is too big), and `llms.txt`."""

from django.urls import path, re_path

from . import sitemap_views
from .sitemap_engine import KIND_PATTERN

# The language is a slug that may hold a hyphen (`pt-br`): the type name ends the file name.
urlpatterns = [
    path("sitemap.xml", sitemap_views.sitemap_index, name="sitemap"),
    re_path(
        rf"^sitemap-(?P<language>[a-zA-Z-]+)-(?P<kind>{KIND_PATTERN})-(?P<part>[0-9]+)\.xml$",
        sitemap_views.sitemap_part,
        name="sitemap_part",
    ),
    re_path(
        rf"^sitemap-(?P<language>[a-zA-Z-]+)-(?P<kind>{KIND_PATTERN})\.xml$",
        sitemap_views.sitemap_section,
        name="sitemap_section",
    ),
    path("llms.txt", sitemap_views.llms_txt, name="llms_txt"),
    path("llms-<slug:language>.txt", sitemap_views.llms_txt, name="llms_language"),
]
