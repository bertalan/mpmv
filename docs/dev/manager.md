# The Manager shell (CORE.17)

The Manager is the one management area for vendor members and platform staff: `apps/manager/`, an
adapter app (views, templates, navigation registry, no business logic). Spec: spec 02, "The
Manager (CORE.17)". Roles and the permission matrix of the platform space come from CORE.18.

## Two spaces, one gate each

| Space | URL prefix | Who |
| --- | --- | --- |
| Shop | `/manager/shop/` | Vendor owner and staff, on one selected vendor |
| Platform | `/manager/platform/` | Platform staff roles and the platform owner |

`apps/manager/spaces.py` holds the prefixes. `ManagerGateMiddleware` (`middleware.py`) runs on every
URL under `/manager/`: a path outside both prefixes is 404, an anonymous visitor goes to the login,
a signed-in user without the space's right gets 404 (never 403), a session without a second factor
goes to enrolment, a view without a registered section is 404, and the section's own check decides
the rest. Members of a closed vendor get a 403 page with no vendor data. `access.py` builds the
per-request `ManagerContext`.

## Adding a page

1. Register a `Section` in `apps/manager/sections.py` with its space, label, URL name and an
   `allowed` check (required, no default; `anyone` must be named on purpose).
2. Mount the view under the space's URLconf in `apps/manager/urls.py`; the view names its section.
3. For a table, subclass `ManagerListView` (`lists.py`): columns, filters and bulk actions. Every
   bulk action runs a service with `dry_run`, shows the `ChangeSet`, and confirms (with MFA step-up
   when sensitive). Rows always pass through `scoped()` so only the selected vendor's data shows.

Business logic stays in the owning app's `services.py`; the Manager only calls it.

## The vendor choice

A member of several vendors picks one (`vendor_choice.py`, a vendor public id in the session). It
is re-checked against the memberships on every request; a forged or stale value is forgotten and
answers 404. Closed vendors are never offered.

## Guards

- `checks.manager_route_problems` and the `manager.E001` system check fail on any route under
  `/manager/` that is outside exactly one space prefix, is a regex, or names no registered section.
- Tests: `apps/manager/tests/unit/` (spaces, navigation, URL sweep) and
  `apps/manager/tests/integration/` (gate, closed vendor, vendor picker, list view, login, sections).
