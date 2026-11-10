# Editor roles (CORE.8, owner decision Q-58)

Wagtail's default "Editors" and "Moderators" groups are removed by migration
`apps/cms/migrations/0012_editor_roles.py`. Our own roles replace them. The data is
`apps/cms/roles.py`; `apps/cms/editor_roles.py` turns it into groups and grants.

| Role | Works on | Page permissions |
| --- | --- | --- |
| Page editors | standard pages, term and vendor landing pages, the vendor recruitment page | change, publish, lock, unlock |
| Blog editors | the blog index and every post under it; blog authors (snippet) | add, change, publish, lock, unlock |
| Legal page editors | the customer legal and checkout pages | change, publish, lock, unlock |
| Event moderators | the events index and every event; the "Event review" workflow task | change, publish |

All roles open the Wagtail admin; all but the event moderators also choose and upload images
and submit pages for translation. The home page is in no role: a Wagtail page grant inherits
downwards and would reach the whole tree. Whoever must edit it is a superuser for now.

## How grants are kept

- After every `migrate`, `sync_editor_roles` creates the groups and adds the permissions and one
  page grant per matching page, one per language.
- A page created later in a matching part of the tree (a new language, the seed, an editor)
  gets its grants from a `post_save` signal.
- The sync only adds. A platform owner who narrows a role in the staff admin keeps the change.
  The drift check for the platform roles is CORE.18.

## What happens to existing users

The data migration moves the members of the default groups so nobody loses access silently:

- "Editors" members join Page editors, Blog editors and Legal page editors.
- "Moderators" members join those three and Event moderators.
- The default "Moderators approval" workflow task keeps working: the three content roles are
  added to its groups.

The new roles are narrower than the defaults, which could edit the whole tree (and, for
Moderators, publish on all of it). After the migrate, review who is in which role in the staff
admin (superuser only). The migration is idempotent and a database without the default groups
(STAGE, a fresh install) is untouched. Reverse: the default groups return with Wagtail's initial
permissions and the members of the three content roles go back to "Editors"; "Moderators"
members are not restored.

## Wagtail admin menu

The menu holds CMS entries only (`ADMIN_MENU_ALLOWED` in `roles.py`): pages, images, navigation
and blog author snippets, reports, settings, help, image classification and the event review.
Anything an app or package adds that is not on the list is dropped, for superusers too.
Vendors, products, orders, rates and payouts are in the Manager (CORE.17). The "Vendor policies"
entry of the Events menu is gone; its page is still reachable by URL until the Manager takes it
over.
