"""Manager routes (CORE.17), mounted at `/manager/` without a language prefix.

Every route sits under exactly one space prefix, `shop/` or `platform/` (`spaces.PREFIXES`); the
sweep in `checks.py` fails on anything else. Sign-in and sign-out live in `account_urls.py`,
outside `/manager/`.
"""

from django.urls import include, path

from . import (
    amendment_views,
    event_block_views,
    event_views,
    fitment_views,
    media,
    notice_views,
    product_views,
    return_policy_views,
    sales_terms_views,
    session_views,
    statement_views,
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
        path(
            "return-policy/", return_policy_views.ReturnPolicyView.as_view(), name="return_policy"
        ),
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
        # EVT.8: "Add translation" of an event.
        path(
            "events/<uuid:public_id>/translate/",
            event_views.EventTranslateView.as_view(),
            name="event_translate",
        ),
        # EVT.4 part 2: the body blocks, the image library and the preview.
        path("events/images/", event_block_views.EventImagesView.as_view(), name="event_images"),
        path(
            "events/<uuid:public_id>/blocks/add/",
            event_block_views.EventBlockAddView.as_view(),
            name="event_block_add",
        ),
        path(
            "events/<uuid:public_id>/blocks/<uuid:block_id>/",
            event_block_views.EventBlockEditView.as_view(),
            name="event_block_edit",
        ),
        path(
            "events/<uuid:public_id>/blocks/<uuid:block_id>/move/",
            event_block_views.EventBlockMoveView.as_view(),
            name="event_block_move",
        ),
        path(
            "events/<uuid:public_id>/blocks/<uuid:block_id>/remove/",
            event_block_views.EventBlockRemoveView.as_view(),
            name="event_block_remove",
        ),
        path(
            "events/<uuid:public_id>/preview/",
            event_block_views.EventPreviewView.as_view(),
            name="event_preview",
        ),
        path("orders/", views.VendorOrderListView.as_view(), name="orders"),
        path("orders/<uuid:public_id>/", views.VendorOrderDetailView.as_view(), name="order"),
        # ORD.20 part 3b: the shop records that it issued its invoice (spec 07, Invoicing).
        path(
            "orders/<uuid:public_id>/invoice-issued/",
            views.VendorInvoiceIssuedView.as_view(),
            name="order_invoice_issued",
        ),
        # ORD.20 part 3: change a paid order (draft, preview, apply with the passkey, discard).
        # Before `order_action`, whose slug would also match "amend".
        path(
            "orders/<uuid:public_id>/amend/",
            amendment_views.ShopAmendView.as_view(),
            name="order_amend",
        ),
        path(
            "orders/<uuid:public_id>/amend/<uuid:amendment>/apply/",
            amendment_views.ShopAmendmentApplyView.as_view(),
            name="amendment_apply",
        ),
        path(
            "orders/<uuid:public_id>/amend/<uuid:amendment>/discard/",
            amendment_views.ShopAmendmentDiscardView.as_view(),
            name="amendment_discard",
        ),
        path(
            "orders/<uuid:public_id>/amend/cancel/",
            amendment_views.ShopCancelView.as_view(),
            name="order_cancel",
        ),
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
        # The statements of reasons sent to the shop (DSA art. 17), read only.
        path(
            "statements/",
            statement_views.VendorStatementsView.as_view(),
            name="statements",
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
        # ORD.20 part 3: the staff tools on a paid order (Support and the platform owner).
        path("orders/", amendment_views.PlatformOrdersView.as_view(), name="orders"),
        path(
            "orders/<uuid:public_id>/",
            amendment_views.PlatformAmendView.as_view(),
            name="order",
        ),
        path(
            "orders/<uuid:public_id>/amendments/<uuid:amendment>/apply/",
            amendment_views.PlatformAmendmentApplyView.as_view(),
            name="amendment_apply",
        ),
        path(
            "orders/<uuid:public_id>/amendments/<uuid:amendment>/discard/",
            amendment_views.PlatformAmendmentDiscardView.as_view(),
            name="amendment_discard",
        ),
        path(
            "orders/<uuid:public_id>/cancel/",
            amendment_views.PlatformCancelView.as_view(),
            name="order_cancel",
        ),
        # The three order-amendment settings, read-only (configuration role and the owner).
        path(
            "settings/order-changes/",
            amendment_views.AmendmentSettingsView.as_view(),
            name="amendment_settings",
        ),
        path(
            "terminations/<uuid:public_id>/",
            termination_views.TerminationDetailView.as_view(),
            name="termination",
        ),
        # FIT.1 part 2: the fitment scheme, its categories and its nodes.
        path("fitment/scheme/", fitment_views.SchemeView.as_view(), name="fitment_scheme"),
        path(
            "fitment/categories/",
            fitment_views.CategoryList.as_view(),
            name="fitment_categories",
        ),
        path("fitment/nodes/", fitment_views.NodeList.as_view(), name="fitment_nodes"),
        path(
            "fitment/nodes/proposals/",
            fitment_views.ProposalList.as_view(),
            name="fitment_proposals",
        ),
        path("fitment/nodes/new/", fitment_views.NodeCreateView.as_view(), name="fitment_node_new"),
        path(
            "fitment/nodes/<uuid:public_id>/",
            fitment_views.NodeDetailView.as_view(),
            name="fitment_node",
        ),
        path(
            "fitment/nodes/<uuid:public_id>/merge/",
            fitment_views.NodeMergeView.as_view(),
            name="fitment_node_merge",
        ),
        # CAT.11: the Catalog section (list with bulk actions, product preview and audited fields).
        path(
            "catalog/products/",
            product_views.ProductList.as_view(),
            name="catalog_products",
        ),
        path(
            "catalog/products/<uuid:public_id>/",
            product_views.ProductDetailView.as_view(),
            name="catalog_product",
        ),
    ],
    "platform",
)

urlpatterns = [
    path("shop/", include(shop)),
    path("platform/", include(platform)),
]
