"""Manager routes (CORE.17), mounted at `/manager/` without a language prefix.

Every route sits under exactly one space prefix, `shop/` or `platform/` (`spaces.PREFIXES`); the
sweep in `checks.py` fails on anything else. Sign-in and sign-out live in `account_urls.py`,
outside `/manager/`.
"""

from django.urls import include, path

from . import media, notice_views, views

app_name = "manager"

shop = (
    [
        path("", views.ShopDashboardView.as_view(), name="dashboard"),
        path("profile/", views.StorefrontProfileView.as_view(), name="profile"),
        path("orders/", views.VendorOrderListView.as_view(), name="orders"),
        path("orders/<uuid:public_id>/", views.VendorOrderDetailView.as_view(), name="order"),
        path(
            "orders/<uuid:public_id>/<slug:action>/",
            views.VendorOrderActionView.as_view(),
            name="order_action",
        ),
        path("vendor/", views.VendorPickerView.as_view(), name="vendor"),
        # The image chooser of the storefront editor (VEN.31, D-34): Wagtail's modal protocol.
        path("media/images/", media.ManagerImageChooseView.as_view(), name="image_choose"),
        path(
            "media/images/results/", media.ManagerImageResultsView.as_view(), name="image_results"
        ),
        path("media/images/create/", media.ManagerImageUploadView.as_view(), name="image_create"),
        path(
            "media/images/chosen/<int:pk>/",
            media.ManagerImageChosenView.as_view(),
            name="image_chosen",
        ),
        path(
            "media/images/<int:image_id>/select_format/",
            media.ManagerImageSelectFormatView.as_view(),
            name="image_select_format",
        ),
        # Documents (VEN.31 part 2) and the external link dialog of the same editor.
        path("media/documents/", media.ManagerDocumentChooseView.as_view(), name="document_choose"),
        path(
            "media/documents/results/",
            media.ManagerDocumentResultsView.as_view(),
            name="document_results",
        ),
        path(
            "media/documents/create/",
            media.ManagerDocumentUploadView.as_view(),
            name="document_create",
        ),
        path(
            "media/documents/chosen/<int:pk>/",
            media.ManagerDocumentChosenView.as_view(),
            name="document_chosen",
        ),
        path("media/links/", media.ManagerLinkView.as_view(), name="link_choose"),
    ],
    "shop",
)

platform = (
    [
        path("", views.PlatformDashboardView.as_view(), name="dashboard"),
        path("profile-changes/", views.ProfileChangesView.as_view(), name="profile_changes"),
        path(
            "profile-changes/<uuid:entry>/revert/",
            views.ProfileRevertView.as_view(),
            name="profile_revert",
        ),
        path(
            "profile-changes/<uuid:entry>/confirm/",
            views.ProfileConfirmView.as_view(),
            name="profile_confirm",
        ),
        path("notices/", notice_views.NoticeListView.as_view(), name="notices"),
        path("notices/<uuid:public_id>/", notice_views.NoticeDetailView.as_view(), name="notice"),
    ],
    "platform",
)

urlpatterns = [
    path("shop/", include(shop)),
    path("platform/", include(platform)),
]
