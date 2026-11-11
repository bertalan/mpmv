"""The Manager's sections, in menu order (CORE.17, spec 02 §Backoffice "Navigation per space").

Registered when the app is ready. Later tasks add theirs here, next to their views: the shop
pages of CORE.9, VEN.9, VEN.15, VEN.20, EVT.4, ORD.6 and CAT.18, and the platform sections
whose role checks CORE.18 defines.
"""

from django.utils.translation import gettext_lazy as _

from .migration_views import PLATFORM_SECTION as MIGRATION_PLATFORM_SECTION
from .migration_views import SHOP_SECTION as MIGRATION_SHOP_SECTION
from .migration_views import can_read_choices
from .navigation import Registry, Section, anyone, vendor_action
from .notice_views import SECTION as NOTICES_SECTION
from .notice_views import can_review
from .spaces import Space

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
    # MIG.16: the owner chooses what to migrate; it is personal data scope, so owners only.
    Section(
        MIGRATION_SHOP_SECTION,
        Space.SHOP,
        _("Migration choices"),
        "manager:shop:migration",
        order=40,
        allowed=vendor_action("profile"),
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
    Section(
        NOTICES_SECTION,
        Space.PLATFORM,
        _("Notices"),
        "manager:platform:notices",
        order=60,
        allowed=can_review,
    ),
    Section(
        MIGRATION_PLATFORM_SECTION,
        Space.PLATFORM,
        _("Migration choices"),
        "manager:platform:migration_choices",
        order=70,
        allowed=can_read_choices,
    ),
)


def register_built_in(registry: Registry) -> None:
    for section in BUILT_IN:
        if registry.get(section.key) is None:
            registry.register(section)
