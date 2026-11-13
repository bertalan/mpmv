"""Routes of the seo app: the temporary staff "Site links" page (OPS.12; remove before LIVE).

Included by name from the root URLconf, so the apps below seo that import `mpmv.urls` in their
test URLconfs do not import seo.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.site_links_page, name="site_links"),
]
