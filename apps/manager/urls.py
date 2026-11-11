"""Manager routes (CORE.17), mounted at `/manager/` without a language prefix.

Every route sits under exactly one space prefix, `shop/` or `platform/` (`spaces.PREFIXES`); the
sweep in `checks.py` fails on anything else. Sign-in and sign-out live in `account_urls.py`,
outside `/manager/`.
"""

from django.urls import include, path

from . import media, migration_views, notice_views, views

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
        path(
            "migration/",
            migration_views.ShopMigrationChoiceView.as_view(),
            name="migration",
        ),
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
    ],
    "shop",
)

platform = (
    [
        path("", views.PlatformDashboardView.as_view(), name="dashboard"),
        path("notices/", notice_views.NoticeListView.as_view(), name="notices"),
        path("notices/<uuid:public_id>/", notice_views.NoticeDetailView.as_view(), name="notice"),
        path(
            "migration/",
            migration_views.PlatformMigrationListView.as_view(),
            name="migration_choices",
        ),
        path(
            "migration/<uuid:public_id>/",
            migration_views.PlatformMigrationChoiceView.as_view(),
            name="migration_choice",
        ),
    ],
    "platform",
)

urlpatterns = [
    path("shop/", include(shop)),
    path("platform/", include(platform)),
]
