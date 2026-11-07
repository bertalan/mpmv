# Audit trail

Every sensitive mutation writes an append-only `AuditLog` entry: who, what, before/after and
from where (spec 01 conventions, spec 12 SEC.7, backlog ARC.6 and SEC.4). This page lists what
is audited today, the rules new code must follow, and how rate models (TAX, SHP, VEN
commissions) hook in.

## Reading the log

Staff open *Core → Audit logs* in the admin (`/django-admin/core/auditlog/`). The view is
read-only (no add, change, delete or bulk actions; the database refuses updates and deletes
anyway) and asks for a fresh second factor: it is wrapped in the CORE.4 step-up named by
`settings.STEP_UP_DECORATOR`.

Filters, which combine:

| Filter | Query parameter | Matches |
|---|---|---|
| Actor | `actor` | `actor_label` contains the text (`staff:alice@`, `anonymous`, `api:`...) |
| Action | `action` | one action, e.g. `vendor.updated` |
| Object type | `target_model` | one model label, e.g. `core.platformoperator` |
| Source | `source__exact` | admin, vendor, api, command, task, mcp, system |
| Date range | `created_from`, `created_to` | creation date between the two days (inclusive, `YYYY-MM-DD`) |
| Vendor | `vendor` | entries about one vendor (its public id) |

The detail page shows a *Changes* table (field, before, after) above the raw snapshots. A
vendor's change page links to its trail (`?vendor=<public id>`).

## What is audited

| Area | Actions |
|---|---|
| Logins (CORE.5) | `accounts.login.succeeded`, `accounts.login.failed`, `accounts.login.locked_out`, `accounts.registration.rate_limited` |
| Second factors (CORE.4) | `accounts.mfa.enrolled`, `.verified`, `.verification_failed`, `.recovery_code_used`, `.recovery_codes_generated`, `.device_removed`, `.disabled` |
| Users and permissions (admin) | `accounts.user.created`, `.updated`, `.deleted`, `.password_changed`, `.access_changed`; `auth.group.created`, `.updated`, `.deleted`, `.access_changed` |
| API keys (CORE.6) | `api.client.issued`, `.rotated`, `.revoked` |
| Vendors (VEN.1, CORE.2) | `vendor.created`, `.updated`, `.deleted`, `.status_changed`, `.member_added`, `.member_role_changed`, `.member_removed` |
| Platform configuration (ARC.7) | `core.platform_operator.configured` |
| Product prices (CAT.1) | `catalog.product.price_changed`, `catalog.variant.price_changed` |
| Shipping configuration (SHP.1, admin) | `shipping.<model>.created`, `.updated`, `.deleted` for carriers, zones, vendor rates, weight bands and pickup options |

Deliberately not stored: password hashes (only `has_usable_password`), API key hashes, TOTP
secrets, and the identifier typed in a failed login that matches no account (it may be a typo
of a real address or a password typed in the wrong field; the entry's key is `unknown`).
Failures refused during a lockout are not audited one by one: the lockout entry covers them.

## Rules for new code

1. **Classify every model.** Each app's `AppConfig` lists `audited_models` and
   `unaudited_models` (`{name: reason}`); models of other packages that the app manages in the
   admin use their full label (`"auth.group"`). `manage.py check` (and so `make check` and CI)
   fails with `core.E201` for an unclassified model and `core.E202` for an exemption without a
   reason.
2. **Write audited models only through services.** A mutating service takes `ctx:
   AuditContext` and `dry_run`, builds a `ChangeSet` and applies it with `run_mutation` (or
   `save_audited` for a plain create-or-update). `tests/unit/test_audit_coverage.py` fails for
   a service that offers `dry_run` without `ctx`, unless it is listed there with the reason.
   Views and admins build `ctx` with `apps.core.audit.audit_context(request)`, which records
   the trusted-proxy client IP (`apps.core.http.client_ip`); do not write another helper. The
   API builds its own context, because its actor is the API client.
3. **Admins call those services.** The admin of an audited model overrides `save_model`,
   `delete_model` and `delete_queryset` (plus `save_related` for many-to-many fields and
   `save_formset` for audited inlines), or denies the permission. Otherwise the system check
   fails with `core.E203`. Admin actions must call services too (see `vendors.admin`).
   Site-level admin views are not ModelAdmins, so the check cannot see them: the staff site
   (`apps.accounts.admin_site.AuditedAdminSite`, installed as `admin.site`) audits the
   self-service password change, and any new site-level view that writes must do the same.
4. **Snapshots** are plain values: public ids instead of foreign keys, no secrets or hashes,
   `Decimal` kept exact (stored as strings). Objects owned by a vendor include
   `"vendor": <vendor public id>`, which fills the entry's `vendor_key` for the vendor filter.
5. **Key entries by a stable id** (`public_id`), never by an editable slug or name, unless the
   model has nothing else (Django groups use their unique name).
6. **Sensitive views** (IBAN, payouts, rates, data exports) use `@step_up_required` (CORE.4).

## Hooking in rate models (TAX, SHP, commissions)

Rates, zones and commission rules are versioned configuration, like `PlatformOperator`: a
change is a new row valid from a date, closing the current one, so orders and documents keep
pointing at the version they were computed with. The audit entry of the new row carries the
row it replaces as `before`, so the staff view shows the rate change with before/after:

```python
TAX_RATE_MODEL = "tax.taxrate"
RATE_CHANGED = "tax.rate.changed"
RATE_FIELDS = ("country", "category", "rate", "valid_from", "valid_to")


def rate_snapshot(rate: TaxRate) -> dict[str, Any]:
    return {name: getattr(rate, name) for name in RATE_FIELDS}


def change_rate(new: TaxRate, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    current = current_rate(new.country, new.category, at=new.valid_from)  # row locked
    changeset = ChangeSet()
    changeset.update(
        TAX_RATE_MODEL,
        str(current.public_id),
        {"valid_to": current.valid_to},
        {"valid_to": new.valid_from},
    )
    changeset.create(
        TAX_RATE_MODEL, str(new.public_id), rate_snapshot(new), supersedes=rate_snapshot(current)
    )

    def apply() -> None:
        current.valid_to = new.valid_from
        current.save(update_fields=["valid_to", "updated_at"])
        new.save()

    return run_mutation(changeset, apply, ctx=ctx, action=RATE_CHANGED, dry_run=dry_run)
```

Then add `"taxrate"` to the app's `audited_models`, route its admin through `change_rate`, and
add a regression test that a rate change is listed under `?target_model=tax.taxrate` with the
old and new rate in its *Changes* table (see
`apps/core/tests/integration/test_audit_admin.py` for the platform operator version). A
vendor-specific shipping zone or commission rule also puts `"vendor"` in its snapshot.
