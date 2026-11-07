# Feature flags and kill switches

Backlog ARC.8 (spec 01). Flags have defaults per environment, staff switches in the admin and
per-vendor overrides. Three of them are kill switches: `checkout`, `import` and `payments`.

## Reading a flag

Use the one helper, `apps.core.services.flag_enabled`:

```python
from apps.core import flags
from apps.core.services import flag_enabled

if not flag_enabled(flags.CHECKOUT, vendor=vendor):
    raise CheckoutClosed  # the caller's own error and message
```

`vendor` is optional. Leave it out for platform-wide features. When given, it must be a
`vendors.Vendor`; any other model raises `TypeError`. A name nobody registered raises
`flags.UnknownFlag`, so a typo fails loudly and never reads as "off".

## Declaring a flag

Flags are declared in code. The kill switches are in `apps/core/flags.py`. A new flag is
registered once, while Django loads the apps: at import time of a module that app loading
imports (the app's `models.py`, or a `flags.py` it imports), or in the app's
`AppConfig.ready()`:

```python
BETA_SEARCH = flags.register(
    flags.FlagSpec("beta_search", "Search with typo tolerance.", default=False)
).name
```

Registering in a module that is imported only on first use (a view, a task) is not enough: a
process that has not imported it yet treats the flag as unknown, and the admin does not offer
it. `manage.py check` fails with `core.E402` when a flag was registered after the apps loaded
(for example by a views module imported while the URLconf is checked).

Names are lowercase identifiers, at most 64 characters. A kill switch is a flag with
`default=True, kill_switch=True` that is turned off to stop something in an emergency.

## How the value is resolved

Each step overrides the previous one when it says something:

| Step | Where | Values |
|---|---|---|
| 1. Code default | `FlagSpec.default` | on / off |
| 2. Environment default | `settings.FEATURE_FLAGS`, read from `MPMV_FEATURE_FLAGS` (`checkout=off,beta_search=on`) | on / off, or unset |
| 3. Staff switch | `FeatureFlag` row, *Core → Feature flags* in the admin | environment default / on / off |
| 4. Vendor override | `VendorFeatureFlag` row, *Vendors → Vendor feature flags* | on / off for one vendor |

One exception for kill switches: a vendor override can turn one off for that vendor, but it
never turns it back on when the platform value is off. When staff pull a kill switch, the feature
stops for every vendor. An override that turns a kill switch on would never change anything,
so it is refused.

`manage.py check` fails with `core.E401` when `MPMV_FEATURE_FLAGS` names a flag that is not
registered, so a misspelled kill switch is caught at start-up.

The test settings pin `FEATURE_FLAGS = {}`, so tests always start from the code defaults.

## Storage

- `core.FeatureFlag`: one row per flag that staff have touched. The kill switch rows are seeded
  by migration `core.0008`, so nobody has to create one during an incident. Rows are never
  deleted. To go back, set the state to "Environment default". A row whose flag was removed
  from code is ignored.
- `vendors.VendorFeatureFlag`: vendor-owned rows (CORE.3 scoping, `vendor_scope = "vendor"`),
  one per vendor and flag. Deleting the row puts the vendor back on the platform value. Core
  cannot import vendors, so it reads these rows through the `vendor_overrides` related name
  (`apps.core.models.VENDOR_OVERRIDES`).

Both are written only through audited services (`save_flag`, `save_vendor_flag`,
`delete_vendor_flag`), which accept `dry_run=True`. The audit actions are listed in
[audit-trail.md](audit-trail.md).

## Admin and step-up

Staff manage flags in the Django admin:

- *Core → Feature flags* lists every flag row with its description, whether it is a kill
  switch, this environment's default and the effective platform value. The name is chosen from
  the registered flags when a row is added, and cannot be changed afterwards.
- *Vendors → Vendor feature flags* holds the per-vendor overrides. The vendor and the flag are
  chosen when an override is added and cannot be changed afterwards; to move an override,
  delete it and add a new one.

Every page that can change a kill switch asks for a fresh second factor (CORE.4 step-up,
`settings.STEP_UP_DECORATOR`): the change page of a kill switch, the change and delete pages of
a kill switch override, and both add pages, because the flag is chosen on them. The change
page of an ordinary override skips the step-up, which is safe only because its flag is
read-only there. The overrides
have no bulk delete action, since it would bypass the step-up.

## How fast a change takes effect

Each process (gunicorn worker, task worker) caches every stored switch and override in one
snapshot for `FEATURE_FLAGS_CACHE_SECONDS` (5 seconds by default; 0 turns the cache off, as in
the tests). A snapshot costs two small queries.

- In the process that saved the change, the cache is dropped when the transaction commits, so
  the change applies to the next check.
- Every other process picks it up within `FEATURE_FLAGS_CACHE_SECONDS`.

So a pulled kill switch stops checkout, imports or payments everywhere within 5 seconds. There
is no shared cache to invalidate: PostgreSQL is the only datastore (ADR-0001). Code that must
not start work after a kill switch is pulled checks the flag right before it starts, and long
jobs check it again between batches.
