"""The project URLs plus the test-only Manager sections of `views.py` (CORE.17)."""

from django.urls import path

from mpmv.urls import urlpatterns as project_urlpatterns

from . import views

urlpatterns = [
    path("manager/shop/test-members/", views.MemberList.as_view(), name="test_members"),
    path("manager/shop/test-owner-only/", views.OwnerOnly.as_view(), name="test_owner_only"),
    path("manager/shop/test-wrong-space/", views.PlatformSectionOnShop.as_view()),
    path("manager/shop/test-no-section/", views.no_section),
    path("manager/shop/test-unknown-section/", views.UnknownSection.as_view()),
    *project_urlpatterns,
]
