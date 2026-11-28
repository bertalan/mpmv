"""Public CMS routes, mounted inside `i18n_patterns` (CMS.1, I18N.1).

`home` keeps its URL name for the rest of the code (redirects, links): it is the language's
home page in the Wagtail tree. Every other path is looked up in the tree by Wagtail.
"""

from django.urls import path, re_path
from wagtail import urls as wagtail_urls

from . import views

# Wagtail's own routes (password and login forms of page restrictions) minus its catch-all, which
# `views.serve_page` replaces below to add the I18N.21 fallback.
WAGTAIL_UTILITY_ROUTES = [r for r in wagtail_urls.urlpatterns if r.name != "wagtail_serve"]

urlpatterns = [
    path("", views.home, name="home"),
    *WAGTAIL_UTILITY_ROUTES,
    # Wagtail's catch-all (same pattern and name as `wagtail.urls`), with the I18N.21 fallback.
    re_path(r"^((?:[\w\-]+/)*)$", views.serve_page, name="wagtail_serve"),
]
