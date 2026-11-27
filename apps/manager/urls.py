"""Manager routes (CORE.17), mounted at `/manager/` without a language prefix.

Every route sits under exactly one space prefix, `shop/` or `platform/` (`spaces.PREFIXES`); the
sweep in `checks.py` fails on anything else. Sign-in and sign-out live in `account_urls.py`,
outside `/manager/`.
"""

from django.urls import include, path

from . import (
    amendment_views,
    closure_staff_views,
    closure_views,
    event_block_views,
    event_views,
    fitment_picker,
    fitment_shop_views,
    fitment_views,
    media,
    money_views,
    notice_views,
    product_views,
    return_policy_views,
    sales_terms_views,
    session_views,
    staff_draft_views,
    statement_views,
    stray_views,
    termination_views,
    trust_edit_views,
    trust_views,
    views,
)

app_name = "manager"

shop = (
    [
        path("", views.ShopDashboardView.as_view(), name="dashboard"),
        path("profile/", views.StorefrontProfileView.as_view(), name="profile"),
        path("payments/", views.PaymentsView.as_view(), name="payments"),
        # VEN.9: the vendor's own money, read only (the export is a POST, audited).
        path("money/", money_views.MoneyOverviewView.as_view(), name="money"),
        path(
            "money/transactions/",
            money_views.TransactionListView.as_view(),
            name="money_transactions",
        ),
        path("money/payouts/", money_views.PayoutListView.as_view(), name="money_payouts"),
        path("money/statement/", money_views.StatementView.as_view(), name="money_statement"),
        path("money/export/", money_views.ExportView.as_view(), name="money_export"),
        path("sales-terms/", sales_terms_views.SalesTermsView.as_view(), name="sales_terms"),
        path(
            "return-policy/", return_policy_views.ReturnPolicyView.as_view(), name="return_policy"
        ),
        # VEN.20: the shop's holidays and closures (list and add, edit, remove).
        path("settings/closures/", closure_views.ClosuresView.as_view(), name="closures"),
        path(
            "settings/closures/<uuid:public_id>/",
            closure_views.ClosureEditView.as_view(),
            name="closure_edit",
        ),
        path(
            "settings/closures/<uuid:public_id>/remove/",
            closure_views.ClosureDeleteView.as_view(),
            name="closure_remove",
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
        # FIT.3: the Fits pages of the shop (list with bulk add, product tab, proposals, report).
        path("fitment/", fitment_shop_views.FitmentList.as_view(), name="fitment"),
        path(
            "fitment/proposals/",
            fitment_shop_views.FitmentProposalsView.as_view(),
            name="fitment_proposals",
        ),
        path(
            "fitment/report/", fitment_shop_views.FitmentReportView.as_view(), name="fitment_report"
        ),
        path("fitment/nodes/", fitment_picker.FitmentNodeChildren.as_view(), name="fitment_nodes"),
        path(
            "fitment/<uuid:public_id>/",
            fitment_shop_views.FitmentProductView.as_view(),
            name="fitment_product",
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
        # TRU.1 part 2: the trust rules versions, read only.
        path("trust-rules/", trust_views.TrustRulesListView.as_view(), name="trust_rules"),
        path(
            "trust-rules/new/", trust_edit_views.TrustRuleNewView.as_view(), name="trust_rule_new"
        ),
        path(
            "trust-rules/<int:version>/",
            trust_views.TrustRulesDetailView.as_view(),
            name="trust_rule",
        ),
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
        # VEN.20: staff manage the closures of any shop (pick a shop, list, add, edit, remove).
        path("shop-closures/", closure_staff_views.ShopPickerView.as_view(), name="shop_closures"),
        path(
            "shop-closures/<uuid:vendor_id>/",
            closure_staff_views.StaffClosuresView.as_view(),
            name="shop_closures_vendor",
        ),
        path(
            "shop-closures/<uuid:vendor_id>/<uuid:public_id>/",
            closure_staff_views.StaffClosureEditView.as_view(),
            name="shop_closure_edit",
        ),
        path(
            "shop-closures/<uuid:vendor_id>/<uuid:public_id>/remove/",
            closure_staff_views.StaffClosureDeleteView.as_view(),
            name="shop_closure_remove",
        ),
        # PAY.4: the open stray-payment alerts and their manual close (POST, a reason).
        path("stray-payments/", stray_views.StrayPaymentsView.as_view(), name="stray_payments"),
        # ORD.32: a cart prepared from a past order, with its one-time link (Support, owner).
        path("order-drafts/", staff_draft_views.DraftListView.as_view(), name="order_drafts"),
        path(
            "order-drafts/new/<uuid:order>/",
            staff_draft_views.DraftCreateView.as_view(),
            name="order_draft_new",
        ),
        path(
            "order-drafts/<uuid:public_id>/",
            staff_draft_views.DraftView.as_view(),
            name="order_draft",
        ),
        path(
            "order-drafts/<uuid:public_id>/lines/",
            staff_draft_views.DraftLinesView.as_view(),
            name="order_draft_lines",
        ),
        path(
            "order-drafts/<uuid:public_id>/send/",
            staff_draft_views.DraftSendView.as_view(),
            name="order_draft_send",
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
