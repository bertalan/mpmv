"""The Manager's sections, in menu order (CORE.17, spec 02 §Backoffice "Navigation per space").

Registered when the app is ready. Later tasks add theirs here, next to their views: the shop
pages of CORE.9, VEN.9, VEN.15, VEN.20, EVT.4, ORD.6 and CAT.18, and the platform sections
whose role checks CORE.18 defines.
"""

from django.utils.translation import gettext_lazy as _

from .navigation import Registry, Section, anyone
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
)


def register_built_in(registry: Registry) -> None:
    for section in BUILT_IN:
        if registry.get(section.key) is None:
            registry.register(section)
