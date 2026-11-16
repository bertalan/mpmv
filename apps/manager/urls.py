"""Manager routes (CORE.17), mounted at `/manager/` without a language prefix.

Every route sits under exactly one space prefix, `shop/` or `platform/` (`spaces.PREFIXES`); the
sweep in `checks.py` fails on anything else. Sign-in and sign-out live in `account_urls.py`,
outside `/manager/`.
"""

from django.urls import include, path

from . import (
    event_views,
    media,
    notice_views,
    sales_terms_views,
    session_views,
    termination_views,
    views,
)

app_name = "manager"

shop = (
    [
        path("", views.ShopDashboardView.as_view(), name="dashboard"),
        path("profile/", views.StorefrontProfileView.as_view(), name="profile"),
        path("payments/", views.PaymentsView.as_view(), name="payments"),
        path("sales-terms/", sales_terms_views.SalesTermsView.as_view(), name="sales_terms"),
        # EVT.4: the vendor's own events (list, form, submit, cancel).
        path("events/", event_views.EventsView.as_view(), name="events"),
        path("events/new/", event_views.EventFormView.as_view(), name="event_new"),
        path("events/<uuid:public_id>/", event_views.EventFormView.as_view(), name="event_edit"),
        path(
            "events/<uuid:public_id>/submit/",
            event_views.EventSubmitView.as_view(),
            name="event_submit",
        ),
        path(
            "events/<uuid:public_id>/cancel/",
            event_views.EventCancelView.as_view(),
            name="event_cancel",
        ),
        path("orders/", views.VendorOrderListView.as_view(), name="orders"),
        path("orders/<uuid:public_id>/", views.VendorOrderDetailView.as_view(), name="order"),
        path(
            "orders/<uuid:public_id>/<slug:action>/",
            views.VendorOrderActionView.as_view(),
            name="order_action",
        ),
        path("vendor/", views.VendorPickerView.as_view(), name="vendor"),
        # The 30 days' notice of termination (CMP.5 part 4): the reason, and the data download.
        path(
            "termination/",
            termination_views.VendorTerminationView.as_view(),
            name="termination",
        ),
        path(
            "termination/data/",
            termination_views.VendorDataDownloadView.as_view(),
            name="termination_data",
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
        path("map/", views.PlatformMapView.as_view(), name="map"),
        path("staff/", views.StaffRolesView.as_view(), name="staff_roles"),
        # CORE.24: one user's sessions (Support) and the owner's "End every session".
        path("sessions/", session_views.UserSessionsView.as_view(), name="user_sessions"),
        path("sessions/find/", session_views.UserLookupView.as_view(), name="user_sessions_find"),
        path(
            "sessions/end-every-session/",
            session_views.EndEverySessionView.as_view(),
            name="sessions_panic",
        ),
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
        path("terminations/", termination_views.TerminationListView.as_view(), name="terminations"),
        path(
            "terminations/<uuid:public_id>/",
            termination_views.TerminationDetailView.as_view(),
            name="termination",
        ),
    ],
    "platform",
)

urlpatterns = [
    path("shop/", include(shop)),
    path("platform/", include(platform)),
]
