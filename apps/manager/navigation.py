"""The Manager's navigation registry (CORE.17, spec 02 §Backoffice).

Each section declares its space, its menu label, its URL name and who may open it. The menu
shows only the sections the actor may open, and every Manager view names its section, whose
check the gate and the view both run (404 otherwise): the menu is never the security boundary.

Later tasks plug their pages in by registering a section in `apps/manager/sections.py` and
mounting the views under the space's URLconf:

    registry.register(Section("shop.orders", Space.SHOP, _("Orders and quotes"),
                              "manager:shop:orders", order=20, allowed=vendor_action("orders")))

`allowed` receives the request's `ManagerContext` (`access.py`). It is required, with no default:
a section that forgets it cannot be declared, and `anyone` must be named on purpose. In the shop
space `vendor_action` reads the vendor role matrix (CORE.2); in the platform space CORE.18 adds
the role checks from `roles.py`.
"""

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from django_stubs_ext import StrOrPromise

from apps.vendors import engine as vendors_engine

from .spaces import Space


class Actor(Protocol):
    """What a section check reads: `access.ManagerContext` provides it."""

    @property
    def space(self) -> Space: ...
    @property
    def role(self) -> str | None: ...
    @property
    def vendor(self) -> Any: ...


type Check = Callable[[Any], bool]


def anyone(ctx: Actor) -> bool:
    """Every actor the space's gate let in: declare it explicitly, it is never a default."""
    return True


def vendor_action(action: str) -> Check:
    """Members whose role on the selected vendor may perform `action` (CORE.2 matrix)."""
    checked = vendors_engine.to_action(action)  # an unknown action fails at registration

    def check(ctx: Actor) -> bool:
        return ctx.role is not None and vendors_engine.is_allowed(ctx.role, checked)

    return check


def platform_permission(permission: str) -> Check:
    """Platform staff holding the model permission `permission` (until CORE.18 adds roles)."""

    def check(ctx: Actor) -> bool:
        user = getattr(ctx, "user", None)
        return bool(user is not None and user.is_active and user.has_perm(permission))

    return check


@dataclass(frozen=True)
class Section:
    key: str
    space: Space
    label: StrOrPromise  # a lazy translation
    url_name: str
    order: int = 100
    allowed: Check = field(kw_only=True)  # no default: deny unless a check is declared
    in_menu: bool = True
    # Shop sections work on the selected vendor; the vendor picker is the exception.
    needs_vendor: bool = True

    def allows(self, ctx: Actor) -> bool:
        if ctx.space != self.space:
            return False
        if self.space is Space.SHOP and self.needs_vendor and ctx.vendor is None:
            return False
        return bool(self.allowed(ctx))


class Registry:
    def __init__(self) -> None:
        self._sections: dict[str, Section] = {}

    def register(self, section: Section) -> Section:
        if not callable(section.allowed):
            raise TypeError(f"Manager section {section.key!r} declares no permission check")
        if section.key in self._sections:
            raise ValueError(f"Manager section {section.key!r} is already registered")
        self._sections[section.key] = section
        return section

    def unregister(self, key: str) -> None:
        self._sections.pop(key, None)

    def get(self, key: str) -> Section | None:
        return self._sections.get(key)

    def all(self) -> list[Section]:
        return sorted(self._sections.values(), key=lambda s: (s.space, s.order, s.key))

    def menu(self, ctx: Actor) -> list[Section]:
        """The sections of the actor's space it may open, in menu order."""
        return [s for s in self.all() if s.in_menu and s.allows(ctx)]

    @contextlib.contextmanager
    def extended(self, *sections: Section) -> Iterator[None]:
        """Register `sections` for the duration of the block (tests)."""
        for section in sections:
            self.register(section)
        try:
            yield
        finally:
            for section in sections:
                self.unregister(section.key)


registry = Registry()

# A view's section: `section = "<key>"` on a class-based view, or this attribute on a function.
SECTION_ATTR = "section"


def section_key(view: Callable[..., Any]) -> str | None:
    """The section key a view (as resolved, `as_view()` included) declares, or None."""
    owner = getattr(view, "view_class", view)
    key = getattr(owner, SECTION_ATTR, None)
    return key if isinstance(key, str) and key else None
