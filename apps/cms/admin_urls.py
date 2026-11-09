"""Editors' admin routes that replace Wagtail's own (CMS.1), included before the Wagtail admin.

mpmv/urls.py includes this module by name, ahead of `wagtail.admin.urls`, so these paths win.
"""

from django.urls import URLPattern, path
from django.views.decorators.cache import never_cache
from wagtail.admin.auth import require_admin_access
from wagtail.admin.views.account import LoginView

from .admin_views import EditorAccountView, localize_image_info

# Same decorators as Wagtail's own admin URLs: admin permission check and no caching.
# Wagtail's URL name is kept: its views redirect to it, and the shadowed route has the same path.
urlpatterns: list[URLPattern] = [
    # The editors' login with the captcha and no autocomplete="off" (CMP.9, CORE.5); its form is
    # WAGTAILADMIN_USER_LOGIN_FORM. Wagtail routes its own view here without decorators.
    path(
        "login/", LoginView.as_view(template_name="cms/admin/login.html"), name="wagtailadmin_login"
    ),
    path(
        "account/",
        never_cache(require_admin_access(EditorAccountView.as_view())),
        name="wagtailadmin_account",
    ),
    # wagtail-localize's image preview (I18N.3): the path of Wagtail's former admin API.
    path(
        "api/main/images/<int:pk>/",
        never_cache(require_admin_access(localize_image_info)),
        name="cms_localize_image_info",
    ),
]
