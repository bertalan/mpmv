"""Editors' admin routes of the core app that replace Wagtail's own (THM.12).

mpmv/urls.py includes this module ahead of `wagtail.admin.urls`, so these paths win. Wagtail's
views and templates still reverse its own names (`wagtailadmin_collections:edit`), which give the
same path. The name cannot be kept here: a second include under Wagtail's namespace would take
over the reverse() of the namespace's other routes. tests.tenancy_registry.SHADOWED declares the
shadowed route.
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
