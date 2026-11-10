"""Public CMS routes, mounted inside `i18n_patterns` (CMS.1, I18N.1).

`home` keeps its URL name for the rest of the code (redirects, links): it is the language's
home page in the Wagtail tree. Every other path is looked up in the tree by Wagtail.
"""

from django.urls import include, path
from wagtail import urls as wagtail_urls

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("", include(wagtail_urls)),
]
