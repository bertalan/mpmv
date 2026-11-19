"""The platform map (CORE.30, owner request 2026-11-15): every area of the site as links.

Built from `reverse()` and the Vendor table at request time, so nothing here is a literal path.
The Manager URLs that are not pages (a record's detail, an action, the chooser modals) are named
in `NOT_LISTED`; a test walks the URL resolver and fails when a Manager route is in neither
`SHOP_PAGES`, `PLATFORM_PAGES` nor `NOT_LISTED`, so a new Manager page cannot be forgotten.

Left out on purpose: the Django admin (loopback only) and any host, secret or server detail.
"""

from dataclasses import dataclass
from typing import Final

from django.urls import NoReverseMatch, reverse
from django.utils.translation import get_language, gettext_noop
from django.utils.translation import gettext as _

from apps.catalog import slugs
from apps.vendors.models import Vendor


@dataclass(frozen=True)
class Entry:
    label: str
    path: str
    # Staff cannot open the shop space (there is no impersonation path yet), so its pages are
    # shown as plain text, not as links that would answer 403.
    link: bool = True


@dataclass(frozen=True)
class Group:
    title: str
    entries: tuple[Entry, ...]


type Pages = tuple[tuple[str, str], ...]

STOREFRONT_PAGES: Final[Pages] = (
    (gettext_noop("Home"), "home"),
    (gettext_noop("Cart"), "orders:cart"),
    (gettext_noop("Cookie settings"), "consent_preferences"),
    (gettext_noop("DSA points of contact"), "dsa_contact_points"),
    (gettext_noop("Privacy sub-processors"), "subprocessors"),
    (gettext_noop("Report illegal content"), "dsa_notice"),
    (gettext_noop("Withdrawal"), "withdrawal"),
    (gettext_noop("Consumer information"), "consumer_information"),
    (gettext_noop("Sell with us"), "vendors:apply"),
)
ACCOUNT_PAGES: Final[Pages] = (
    (gettext_noop("Sign in"), "login"),
    (gettext_noop("Two-factor authentication"), "mfa:overview"),
    (gettext_noop("My orders"), "customer_orders"),
    (gettext_noop("Privacy and your data"), "privacy_account"),
)
SHOP_PAGES: Final[Pages] = (
    (gettext_noop("Dashboard"), "manager:shop:dashboard"),
    (gettext_noop("Storefront profile"), "manager:shop:profile"),
    (gettext_noop("Payments"), "manager:shop:payments"),
    (gettext_noop("Money"), "manager:shop:money"),
    (gettext_noop("Transactions"), "manager:shop:money_transactions"),
    (gettext_noop("Payouts"), "manager:shop:money_payouts"),
    (gettext_noop("Statement"), "manager:shop:money_statement"),
    (gettext_noop("Sales terms"), "manager:shop:sales_terms"),
    (gettext_noop("Return policy"), "manager:shop:return_policy"),
    (gettext_noop("Orders"), "manager:shop:orders"),
    (gettext_noop("Events"), "manager:shop:events"),
    (gettext_noop("Fitment"), "manager:shop:fitment"),
    (gettext_noop("Fitment report"), "manager:shop:fitment_report"),
    (gettext_noop("Propose a missing item"), "manager:shop:fitment_proposals"),
    (gettext_noop("Choose a vendor"), "manager:shop:vendor"),
    (gettext_noop("Termination notice"), "manager:shop:termination"),
    (gettext_noop("Statements of reasons"), "manager:shop:statements"),
)
PLATFORM_PAGES: Final[Pages] = (
    (gettext_noop("Dashboard"), "manager:platform:dashboard"),
    (gettext_noop("Platform map"), "manager:platform:map"),
    (gettext_noop("Staff and roles"), "manager:platform:staff_roles"),
    (gettext_noop("User sessions"), "manager:platform:user_sessions"),
    (gettext_noop("End every session"), "manager:platform:sessions_panic"),
    (gettext_noop("Storefront changes"), "manager:platform:profile_changes"),
    (gettext_noop("Notices"), "manager:platform:notices"),
    (gettext_noop("Terminations"), "manager:platform:terminations"),
    (gettext_noop("Fitment scheme"), "manager:platform:fitment_scheme"),
    (gettext_noop("Fitment categories"), "manager:platform:fitment_categories"),
    (gettext_noop("Fitment nodes"), "manager:platform:fitment_nodes"),
    (gettext_noop("Proposed nodes"), "manager:platform:fitment_proposals"),
    (gettext_noop("Products"), "manager:platform:catalog_products"),
    (gettext_noop("Order changes"), "manager:platform:orders"),
    (gettext_noop("Order change settings"), "manager:platform:amendment_settings"),
)
ADMIN_PAGES: Final[Pages] = ((gettext_noop("Editors' admin"), "wagtailadmin_home"),)
# Listed only once the setup wizard exists and has not been completed (ARC.25).
SETUP_PAGES: Final[Pages] = ((gettext_noop("Setup"), "setup:start"),)

# Manager URL names that are not pages: they need a record, change data or serve a modal.
NOT_LISTED: Final = frozenset(
    {
        "manager:shop:order",
        "manager:shop:order_action",
        "manager:shop:order_amend",
        "manager:shop:amendment_apply",
        "manager:shop:amendment_discard",
        "manager:shop:order_cancel",
        "manager:shop:fitment_product",
        "manager:shop:order_invoice_issued",
        "manager:platform:order",
        "manager:platform:amendment_apply",
        "manager:platform:amendment_discard",
        "manager:platform:order_cancel",
        "manager:shop:event_new",
        "manager:shop:event_edit",
        "manager:shop:event_submit",
        "manager:shop:event_cancel",
        "manager:shop:event_translate",
        "manager:shop:event_images",
        "manager:shop:event_block_add",
        "manager:shop:event_block_edit",
        "manager:shop:event_block_move",
        "manager:shop:event_block_remove",
        "manager:shop:event_preview",
        "manager:shop:termination_data",
        "manager:shop:money_export",  # VEN.9: POST only, a CSV download
        "manager:shop:image_choose",
        "manager:shop:image_results",
        "manager:shop:image_create",
        "manager:shop:image_chosen",
        "manager:shop:image_select_format",
        "manager:shop:document_choose",
        "manager:shop:document_results",
        "manager:shop:document_create",
        "manager:shop:document_chosen",
        "manager:shop:link_choose",
        "manager:platform:profile_revert",
        "manager:platform:profile_confirm",
        "manager:platform:notice",
        "manager:platform:termination",
        "manager:platform:user_sessions_find",
        "manager:platform:catalog_product",
        "manager:platform:fitment_node_new",
        "manager:platform:fitment_node",
        "manager:platform:fitment_node_merge",
    }
)


def mapped_names() -> set[str]:
    """The Manager URL names the map lists (the completeness test compares them to the URLs)."""
    return {name for _label, name in (*SHOP_PAGES, *PLATFORM_PAGES)}


def _entries(pages: Pages, *, link: bool = True) -> tuple[Entry, ...]:
    found = []
    for label, name in pages:
        try:
            found.append(Entry(_(label), reverse(name), link))
        except NoReverseMatch:  # the page of a task not built yet is simply not there
            continue
    return tuple(found)


def build_map() -> list[Group]:
    groups = [
        Group(_("Storefront"), _entries(STOREFRONT_PAGES)),
        Group(_("Customer account"), _entries(ACCOUNT_PAGES)),
    ]
    shop = _entries(SHOP_PAGES, link=False)
    segment = slugs.segment_for(slugs.SHOP_SEGMENTS, get_language() or "en")
    for vendor in Vendor.objects.order_by("display_name", "id"):
        entries = list(shop)
        if vendor.status == Vendor.Status.ACTIVE:
            path = reverse("catalog:vendor", kwargs={"segment": segment, "slug": vendor.slug})
            entries.insert(0, Entry(_("Storefront page"), path))
        title = _("Manager shop space: %(vendor)s") % {"vendor": vendor.display_name}
        groups.append(Group(title, tuple(entries)))
    groups.append(Group(_("Manager platform space"), _entries(PLATFORM_PAGES)))
    groups.append(Group(_("Editors' admin"), _entries(ADMIN_PAGES)))
    setup = _entries(SETUP_PAGES)
    if setup:
        groups.append(Group(_("Setup"), setup))
    return groups
