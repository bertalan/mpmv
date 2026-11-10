# Editor roles (CORE.8, owner decision Q-58)

Wagtail's default "Editors" and "Moderators" groups are removed by migration
`apps/cms/migrations/0012_editor_roles.py`. Our own roles replace them. The data is
`apps/cms/roles.py`; `apps/cms/editor_roles.py` turns it into groups and grants.

| Role | Works on | Page permissions |
| --- | --- | --- |
| Page editors | standard pages, term and vendor landing pages, the vendor recruitment page | change, lock, unlock |
| Blog editors | the blog index and every post under it; blog authors (snippet) | add, change, lock, unlock |
| Legal page editors | the customer legal and checkout pages | change, lock, unlock |
| Page publishers, Blog publishers, Legal page publishers | the same parts as the editor role of that name | change, publish, lock, unlock |
| Event moderators | the events index and every event; the "Event review" workflow task | change, publish |

Editing and publishing are separate (lane-a decision): the editor roles never publish, so their
work is submitted for moderation. The publisher roles hold `publish_page` and approve the
default workflow task; only staff assign them, and the migration puts nobody in them.
Event moderators are assigned by staff too.

All roles open the Wagtail admin; the three editor roles also choose and upload images and
submit pages for translation. The home page is in no role: a Wagtail page grant inherits
downwards and would reach the whole tree. Whoever must edit it is a superuser for now.

## How grants are kept

- Each role is seeded once: the first `migrate` that sees it without an `EditorRoleSeed` row
  creates the group, its permissions and one page grant per matching page (one per language),
  then writes the marker. Later migrates skip it, so a platform owner who narrows a role in the
  staff admin keeps the change across deploys. Deleting the marker row seeds the role again.
- A page created later in a matching part of the tree (a new language, the seed, an editor)
  gets the role's page grants from a `post_save` signal. The signal never recreates a group or
  a permission the owner removed. The drift check for the platform roles is CORE.18.
- Publish includes unpublish in Wagtail (no separate permission). Publisher roles and Event
  moderators can therefore take down their own part, legal pages included.
- Nobody approves their own submission: `apps/cms/workflow.py` withdraws the approve action
  (and refuses a forged one) for the person who requested the workflow, superusers included.
  Requesting changes stays allowed.
- Image permissions are on the root collection, as in Wagtail's defaults, so they reach every
  collection. Scoping them is a Manager follow-up (CORE.17).

## What happens to existing users

The data migration moves the members of the default groups; nobody is added to a role that is
not the mapping below, and nobody gets Event moderators implicitly (staff assign it in the
staff admin):

- "Editors" members join Page editors, Blog editors and Legal page editors (edit-only).
- "Moderators" members join the same three edit-only roles. They lose the right to publish and
  approve: staff assign a publisher role (or Event moderators) explicitly after the migrate.
- The default "Moderators approval" workflow task keeps working: the three publisher roles
  (empty after the migration) are added to its groups, never the editor roles.

The new roles are narrower than the defaults in scope (they could edit the whole tree) and in
rights (nobody gains publish or approval). After the migrate, review who is in which role in
the staff admin (superuser only) and assign the publisher roles that are needed. The
migration is idempotent and a database without the default groups (STAGE, a fresh install) is
untouched. Reverse: the default groups return with Wagtail's initial permissions and only users
who hold all three editor roles (what the forward step produced) return to "Editors"; a
person with a single role gets no wider access. "Moderators" members are not restored.

## Wagtail admin menu

The menu holds CMS entries only (`ADMIN_MENU_ALLOWED` in `roles.py`): pages, images, navigation
and blog author snippets, reports, settings, help, image classification and the event review.
Anything an app or package adds that is not on the list is dropped, for superusers too.
Vendors, products, orders, rates and payouts are in the Manager (CORE.17). The "Vendor policies"
entry of the Events menu is gone; its page is still reachable by URL until the Manager takes it
over. Until CORE.17 ships, entries other apps add to the Wagtail menu are hidden for everyone,
superusers included; commerce work does not depend on them (the screens are the Manager's).
