"""Routes that need no help page, shared by the help-button sweeps and the ARC.36 coverage sweep.

One home for the allow-list so a change to it is visible in review, with each reason next to it.
This is not a test module: nothing here is collected.
"""

# The storefront editor's image, document and link choosers answer Wagtail's modal protocol:
# they return modal fragments without the page layout, so there is no help button.
MODAL_FRAGMENTS = (
    "manager:shop:document_choose",
    "manager:shop:document_chosen",
    "manager:shop:document_create",
    "manager:shop:document_results",
    "manager:shop:link_choose",
    "manager:shop:image_choose",
    "manager:shop:image_results",
    "manager:shop:image_create",
    "manager:shop:image_chosen",
    "manager:shop:image_select_format",
)
# Routes that answer a redirect or an action, not a page of the shell.
NOT_PAGES = (
    "manager:shop:order_action",
    "manager:shop:event_submit",  # EVT.4: POST only, redirects to the events list
    "manager:shop:event_block_edit",  # EVT.4 part 2: needs a block id
    "manager:shop:event_block_move",  # EVT.4 part 2: POST only
    "manager:shop:event_block_remove",  # EVT.4 part 2: needs a block id (confirm page)
    "manager:shop:event_preview",  # EVT.4 part 2: the storefront layout, see its own test below
    "manager:platform:profile_revert",  # POST only, redirects to the changes page
    "manager:platform:profile_confirm",  # POST only, redirects to the changes page
    "manager:shop:termination_data",  # a JSON file download, not a page
    "manager:shop:fitment_nodes",  # FIT.3 picker: a JSON list of child nodes, not a page
    "manager:shop:money_export",  # VEN.9: POST only, a CSV file download
    "manager:platform:user_sessions_find",  # POST only, redirects to the sessions page
    # ORD.20 part 3: POST only, they redirect to the amendment page (or show the passkey prompt).
    "manager:shop:amendment_apply",
    "manager:shop:amendment_discard",
    "manager:shop:order_cancel",
    "manager:platform:amendment_apply",
    "manager:platform:amendment_discard",
    "manager:platform:order_cancel",
    # ORD.20 part 3b-1: POST only, records invoice_issued_at and redirects to the order page.
    "manager:shop:order_invoice_issued",
    *MODAL_FRAGMENTS,
)


# The coverage sweep's allow-list: route name -> why it needs no help file.
ALLOWED: dict[str, str] = {
    **{
        name: "not a page: a redirect, POST-only or file route (the account/Manager sweep)"
        for name in NOT_PAGES
        if name not in MODAL_FRAGMENTS
    },
    **{
        name: "a modal fragment of Wagtail's chooser protocol, no page layout"
        for name in MODAL_FRAGMENTS
    },
    "mfa:passkey_auth": "JSON endpoint for the browser's passkey prompt",
    "mfa:passkey_auth_options": "JSON endpoint for the browser's passkey prompt",
    "mfa:passkey_register": "JSON endpoint for the browser's passkey prompt",
    "mfa:passkey_register_options": "JSON endpoint for the browser's passkey prompt",
    "mfa:remove_device": "POST only, redirects to the factors page",
    "orders:cart_add": "POST only, redirects to the cart",
    "orders:cart_mini": "a fragment of the header, not a screen",
}
