"""Editors' admin routes that replace Wagtail's own (CMS.1), included before the Wagtail admin.

mpmv/urls.py includes this module by name, ahead of `wagtail.admin.urls`, so these paths win.
"""

from django.urls import URLPattern, path
from django.views.decorators.cache import never_cache
from wagtail.admin.auth import require_admin_access

from .admin_views import EditorAccountView

# Same decorators as Wagtail's own admin URLs: admin permission check and no caching.
# Wagtail's URL name is kept: its views redirect to it, and the shadowed route has the same path.
urlpatterns: list[URLPattern] = [
    path(
        "account/",
        never_cache(require_admin_access(EditorAccountView.as_view())),
        name="wagtailadmin_account",
    ),
]
