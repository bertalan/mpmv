"""Editors' admin routes of the core app that replace Wagtail's own (THM.12).

mpmv/urls.py includes this module ahead of `wagtail.admin.urls`, so these paths win. Wagtail's
URL names are kept: its views and templates reverse them, and the shadowed route has the same
path.
"""

from django.urls import URLPattern, path
from django.views.decorators.cache import never_cache
from wagtail.admin.auth import require_admin_access

from .admin_views import CollectionEditView

# Same decorators as Wagtail's own admin URLs: admin permission check and no caching.
urlpatterns: list[URLPattern] = [
    path(
        "collections/<int:pk>/",
        never_cache(require_admin_access(CollectionEditView.as_view())),
        name="core_collection_edit",
    ),
]
