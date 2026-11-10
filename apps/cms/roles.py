"""Editor roles of the Wagtail admin (CORE.8, owner decision Q-58): pure data, no ORM.

Each role is a group limited to its part of the page tree. They replace Wagtail's default
"Editors" and "Moderators" groups, which migration `cms/0012_editor_roles` removes. Wagtail
page permissions are granted on a tree node and inherit downwards, so a role is bound to the
page types whose nodes it may change: the blog index (and with it every post), each legal
page, the event indexes (and with them every event), and the standalone content pages. The
home page is deliberately in no role: a grant on it would reach the whole tree.

`apps.cms.editor_roles` turns this data into groups and grants. It adds what is missing and
never removes: the platform owner narrows a role later in the staff admin (CORE.18 adds the
drift check for the platform roles).

The Wagtail admin menu is limited to CMS entries: `ADMIN_MENU_ALLOWED` lists the top-level
items that may appear, whoever the user is. Commerce lives in the Manager (CORE.17).
"""

from dataclasses import dataclass

# Wagtail page permission codenames (wagtailcore.Page).
ADD = "add_page"
CHANGE = "change_page"
PUBLISH = "publish_page"
LOCK = "lock_page"
UNLOCK = "unlock_page"

PAGE_EDITORS = "Page editors"
BLOG_EDITORS = "Blog editors"
LEGAL_EDITORS = "Legal page editors"
EVENT_MODERATORS = "Event moderators"  # also the group of the "Event review" workflow (EVT.3)

# Wagtail's default groups, removed by the data migration.
DEFAULT_EDITORS = "Editors"
DEFAULT_MODERATORS = "Moderators"


@dataclass(frozen=True)
class EditorRole:
    name: str
    page_models: tuple[str, ...]  # "app_label.ModelName": the tree nodes the role works on
    page_permissions: tuple[str, ...]
    model_permissions: tuple[str, ...] = ()  # "app_label.codename" (snippets)
    images: bool = True  # choose and upload images, translate pages


ROLES: tuple[EditorRole, ...] = (
    EditorRole(
        name=PAGE_EDITORS,
        page_models=(
            "cms.StandardPage",
            "cms.TermLandingPage",
            "cms.VendorLandingPage",
            "cms.VendorRecruitmentPage",
        ),
        page_permissions=(CHANGE, PUBLISH, LOCK, UNLOCK),
    ),
    EditorRole(
        name=BLOG_EDITORS,
        page_models=("cms.BlogIndexPage",),
        page_permissions=(ADD, CHANGE, PUBLISH, LOCK, UNLOCK),
        model_permissions=(
            "cms.add_blogauthor",
            "cms.change_blogauthor",
            "cms.delete_blogauthor",
            "cms.view_blogauthor",
        ),
    ),
    EditorRole(
        name=LEGAL_EDITORS,
        page_models=("cms.LegalPage",),
        page_permissions=(CHANGE, PUBLISH, LOCK, UNLOCK),
    ),
    EditorRole(
        name=EVENT_MODERATORS,
        page_models=("events.EventIndexPage",),
        page_permissions=(CHANGE, PUBLISH),
        images=False,
    ),
)

ROLE_NAMES: tuple[str, ...] = tuple(role.name for role in ROLES)

# What a member of Wagtail's default group becomes (the migration moves the users): the three
# content roles for both, plus the event moderators for the default "Moderators".
CONTENT_ROLES: tuple[str, ...] = (PAGE_EDITORS, BLOG_EDITORS, LEGAL_EDITORS)
DEFAULT_GROUP_SUCCESSORS: dict[str, tuple[str, ...]] = {
    DEFAULT_EDITORS: CONTENT_ROLES,
    DEFAULT_MODERATORS: (*CONTENT_ROLES, EVENT_MODERATORS),
}

# Page models whose nodes a role is bound to, for the creation signal.
ROLE_PAGE_MODELS: tuple[str, ...] = tuple(
    sorted({label for role in ROLES for label in role.page_models})
)

# Top-level Wagtail admin menu items allowed (by `MenuItem.name`). No vendor, product, order,
# rate or payout item: staff do that work in the Manager (CORE.17).
ADMIN_MENU_ALLOWED: frozenset[str] = frozenset(
    {
        "explorer",
        "images",
        "navigation",
        "blog-authors",
        "reports",
        "settings",
        "help",
        "events",
        "images-to-classify",
    }
)

# Entries of the "Events" submenu that are not CMS work: the vendors' moderation policies.
EVENTS_SUBMENU_HIDDEN: frozenset[str] = frozenset({"vendor-policies"})
