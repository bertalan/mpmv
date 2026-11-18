"""The Manager's sections, in menu order (CORE.17, spec 02 §Backoffice "Navigation per space").

Registered when the app is ready. Later tasks add theirs here, next to their views: the shop
pages of CORE.9, VEN.9, VEN.15, VEN.20, EVT.4, ORD.6 and CAT.18, and the platform sections
whose role checks CORE.18 defines.
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.vendors.services import can_review_profiles

from .amendment_views import PLATFORM_SECTION as ORDER_CHANGES_SECTION
from .amendment_views import SETTINGS_SECTION as AMENDMENT_SETTINGS_SECTION
from .event_views import SECTION as EVENTS_SECTION
from .fitment_views import CATEGORIES_SECTION, NODES_SECTION, SCHEME_SECTION
from .navigation import Registry, Section, anyone, platform_role, vendor_action
from .notice_views import SECTION as NOTICES_SECTION
from .notice_views import can_review
from .product_views import SECTION as PRODUCTS_SECTION
from .sales_terms_views import SECTION as SALES_TERMS_SECTION
from .session_views import PANIC_SECTION, USER_SESSIONS_SECTION
from .spaces import Space
from .staff_draft_views import SECTION as ORDER_DRAFTS_SECTION
from .statement_views import SECTION as STATEMENTS_SECTION
from .termination_views import PLATFORM_SECTION as TERMINATIONS_SECTION
from .termination_views import SHOP_SECTION as TERMINATION_SECTION
from .termination_views import can_give_notice


def can_review_profiles_section(ctx: Any) -> bool:
    return can_review_profiles(ctx.user)


BUILT_IN = (
    Section(
        "shop.dashboard",
        Space.SHOP,
        _("Dashboard"),
        "manager:shop:dashboard",
        order=10,
        allowed=anyone,
    ),
    # Owner only: the role matrix gives `profile` to owners, so staff get no menu entry and a 404.
    Section(
        "shop.profile",
        Space.SHOP,
        _("Storefront profile"),
        "manager:shop:profile",
        order=20,
        allowed=vendor_action("profile"),
    ),
    # The seller's versioned sales terms (VEN.35): owners only, like the profile.
    Section(
        SALES_TERMS_SECTION,
        Space.SHOP,
        _("Sales terms"),
        "manager:shop:sales_terms",
        order=22,
        allowed=vendor_action("profile"),
    ),
    # The vendor's own events (EVT.4): owners and staff, by the `events` action of the matrix.
    Section(
        EVENTS_SECTION,
        Space.SHOP,
        _("Events"),
        "manager:shop:events",
        order=35,
        allowed=vendor_action("events"),
    ),
    # The payout account (VEN.6): owners only, like the other financial settings.
    Section(
        "shop.payments",
        Space.SHOP,
        _("Payments"),
        "manager:shop:payments",
        order=40,
        allowed=vendor_action("bank_account"),
    ),
    # The image chooser of the storefront editor (VEN.31): a modal, not a page, so not in the
    # menu. Owners only, like the profile it serves.
    Section(
        "shop.media",
        Space.SHOP,
        _("Images"),
        "manager:shop:image_choose",
        order=25,
        in_menu=False,
        allowed=vendor_action("profile"),
    ),
    Section(
        "shop.orders",
        Space.SHOP,
        _("Orders"),
        "manager:shop:orders",
        order=30,
        allowed=vendor_action("orders"),
    ),
    # The vendor picker: reached from the header, so not in the menu, and before any choice.
    Section(
        "shop.vendor",
        Space.SHOP,
        _("Choose a vendor"),
        "manager:shop:vendor",
        order=0,
        in_menu=False,
        needs_vendor=False,
        allowed=anyone,
    ),
    Section(
        "platform.dashboard",
        Space.PLATFORM,
        _("Dashboard"),
        "manager:platform:dashboard",
        order=10,
        allowed=anyone,
    ),
    # CORE.30: the platform map, open to every role that opens the dashboard.
    Section(
        "platform.map",
        Space.PLATFORM,
        _("Platform map"),
        "manager:platform:map",
        order=15,
        allowed=platform_role("platform.map"),
    ),
    # Staff with the revert permission (CORE.9 3/3): history of owners' profile changes, revert.
    Section(
        "platform.profile_changes",
        Space.PLATFORM,
        _("Storefront changes"),
        "manager:platform:profile_changes",
        order=20,
        allowed=can_review_profiles_section,
    ),
    # FIT.1 part 2: the fitment scheme and categories (Configuration and tax), the nodes (Catalog).
    Section(
        SCHEME_SECTION,
        Space.PLATFORM,
        _("Fitment scheme"),
        "manager:platform:fitment_scheme",
        order=41,
        allowed=platform_role(SCHEME_SECTION),
    ),
    Section(
        CATEGORIES_SECTION,
        Space.PLATFORM,
        _("Fitment categories"),
        "manager:platform:fitment_categories",
        order=42,
        allowed=platform_role(CATEGORIES_SECTION),
    ),
    Section(
        NODES_SECTION,
        Space.PLATFORM,
        _("Fitment nodes"),
        "manager:platform:fitment_nodes",
        order=43,
        allowed=platform_role(NODES_SECTION),
    ),
    # CAT.11: the products, for Catalog and moderation (the owner reads).
    Section(
        PRODUCTS_SECTION,
        Space.PLATFORM,
        _("Products"),
        "manager:platform:catalog_products",
        order=44,
        allowed=platform_role(PRODUCTS_SECTION),
    ),
    Section(
        NOTICES_SECTION,
        Space.PLATFORM,
        _("Notices"),
        "manager:platform:notices",
        order=60,
        allowed=can_review,
    ),
    # CMP.5 part 4: notices of termination, for the staff who review notices (CORE.18).
    Section(
        TERMINATIONS_SECTION,
        Space.PLATFORM,
        _("Terminations"),
        "manager:platform:terminations",
        order=65,
        allowed=can_give_notice,
    ),
    # The vendor's view of its notice and the data download: every member of the vendor, so
    # nobody working in the shop is surprised by the end of the service.
    Section(
        TERMINATION_SECTION,
        Space.SHOP,
        _("Termination notice"),
        "manager:shop:termination",
        order=90,
        allowed=anyone,
    ),
    # The statements of reasons the platform sent the shop: every member, read only.
    Section(
        STATEMENTS_SECTION,
        Space.SHOP,
        _("Statements of reasons"),
        "manager:shop:statements",
        order=91,
        allowed=anyone,
    ),
    # ORD.20 part 3: the staff tools on a paid order (Support and the platform owner).
    Section(
        ORDER_CHANGES_SECTION,
        Space.PLATFORM,
        _("Order changes"),
        "manager:platform:orders",
        order=50,
        allowed=platform_role(ORDER_CHANGES_SECTION),
    ),
    # ORD.32: carts prepared from a past order with a one-time link (Support, platform owner).
    Section(
        ORDER_DRAFTS_SECTION,
        Space.PLATFORM,
        _("Prepared carts"),
        "manager:platform:order_drafts",
        order=51,
        allowed=platform_role(ORDER_DRAFTS_SECTION),
    ),
    # ORD.20 part 3: the three amendment settings in force (configuration role and the owner).
    Section(
        AMENDMENT_SETTINGS_SECTION,
        Space.PLATFORM,
        _("Order change settings"),
        "manager:platform:amendment_settings",
        order=85,
        allowed=platform_role(AMENDMENT_SETTINGS_SECTION),
    ),
    Section(
        "platform.staff_roles",
        Space.PLATFORM,
        _("Staff and roles"),
        "manager:platform:staff_roles",
        order=90,
        allowed=platform_role("platform.staff_roles"),
    ),
    # CORE.24: Support and the owner see one user's sessions and end them.
    Section(
        USER_SESSIONS_SECTION,
        Space.PLATFORM,
        _("User sessions"),
        "manager:platform:user_sessions",
        order=70,
        allowed=platform_role(USER_SESSIONS_SECTION),
    ),
    # CORE.24: the platform owner's panic button, last in the menu.
    Section(
        PANIC_SECTION,
        Space.PLATFORM,
        _("End every session"),
        "manager:platform:sessions_panic",
        order=95,
        allowed=platform_role(PANIC_SECTION),
    ),
)


def register_built_in(registry: Registry) -> None:
    for section in BUILT_IN:
        if registry.get(section.key) is None:
            registry.register(section)
