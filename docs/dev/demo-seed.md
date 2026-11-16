# Demo data seed (ARC.10)

`make seed` (or `python manage.py seed`) fills the database with a synthetic demo marketplace,
for local development, the test suites and the STAGE demo (ARC.22). Everything is invented:
vendors are the pseudonyms "Vendor A" to "Vendor E", emails use the reserved `example.org`
domain, addresses are example streets and legal identifiers are synthetic (checksum-valid where
the platform checks one). Nothing comes from the legacy system.

```sh
make seed                              # create what is missing (idempotent)
make seed RESET=1                      # take the seed out again
uv run python manage.py seed --dry-run # report what would change, write nothing
```

## Where it runs

Only where `settings.ALLOW_SEED` is on and `settings.LIVE_DATA` is off:

| Settings | `ALLOW_SEED` | `LIVE_DATA` | The seed |
| --- | --- | --- | --- |
| dev, test | on | off | runs |
| STAGE | `MPMV_ALLOW_SEED=1` in its environment | off | runs only with the variable |
| LIVE | from the environment | on | never; with `MPMV_ALLOW_SEED` set, the `seed.E001` system check fails |

Elsewhere `seed` and `seed --reset` stop with an error before touching the database.

The code is in `apps/seed/`: `data.py` holds the content (pure data), `services.py` builds it,
`images.py` draws the placeholder images with Pillow, as WebP. No app imports `apps.seed`; it sits above
every app in the import contracts.

## What it creates

| Area | Content |
| --- | --- |
| Platform | A demo `PlatformOperator` if none is configured; a demo vendor contract (EN, IT) if none is in force; the platform default commission rule of spec 04 (20% on net goods, payment fee 4% + 2.00 on the gross paid) if no default rule is open, so the demo checkout can place orders; VAT rates for AT, DE, ES, FR and IT where a country has none |
| Vendors | Five active vendors, one per regime (E's "other" exemption names France, the country of its law, TAX.13): A (IT, S.r.l., RF01, domestic-only VAT), B (IT, sole proprietor, RF19 forfettario, exempt), C (IT, S.r.l., RF01, publishing art. 74 exempt), D (DE, GmbH, RF18, OSS), E (FR, RF20 cross-border franchise with an `EX` number, exempt). Each has a complete DAC7 dataset (VEN.23) and went through application, contract acceptance, activation and an approved tax profile; storefront texts in EN and IT |
| Catalog | 15 categories (2 levels; the 5 top-level ones have `demo-` slugs in every language) and a "Material" taxonomy with term pages, named in EN, IT, DE, FR and ES; two variant attributes (size, colour); five brands; 50 published products (10 per vendor) with EN and IT texts, Decimal net prices, some sale prices, 32 variants, two "on request" products |
| Shipping | Two carriers; per vendor a domestic and an EU zone, three methods with weight bands and special-area treatments (island surcharges for Sicily, Sardinia, Corsica; quotes for the minor islands and the Venice lagoon; no delivery to the extra-VAT areas) |
| CMS | The CMS.1 sample site (`seed_cms`) in every language; a demo home body (hero with a placeholder image, material and vendor cards) in every language; "How it works" (`/demo-how-it-works/`, EN, IT); five legal pages marked as demo placeholders (EN, IT) |
| Events | A fair, a promotion and an online workshop, in EN and IT, with placeholder images |

Not seeded yet, because the models do not exist: product images (CAT.4), product pages, orders,
payments and ledger entries. Carts are a shopper's session data and are not seeded.

## Accounts

Vendor owners (`owner.vendor-a@example.org` ...) and the seed operator
(`seed-operator@example.org`) have unusable passwords: no seed account can log in. The operator
is the staff identity that approves the tax profiles and reopens vendors after a reset; it is
active only while a seed runs. Demo logins come with ARC.22.

## Idempotent and reproducible

There is no randomness: the same code produces the same data. Every row is found again by a key
that only the seed uses, and only what is missing is created, so a second run changes nothing
and reports `0 changes`. The keys:

- vendors: the slug *and* the membership of the seed owner (`owner.vendor-…@example.org`); a
  vendor with a seed slug that the seed did not create stops the seed (`SeedConflict`);
- categories: the `demo-` top-level slugs, and the subcategories under them;
- the `demo_` attribute and taxonomy codes, the "Demo Brand" names, the `demo-` carrier codes;
- pages and events: `demo-` slugs; images: their "Demo placeholder" titles and seed author;
- products: the vendor's SKUs (`DEMO-A-01` …).

## Reset

`--reset` removes only rows with the seed's keys, and keeps a classification row that a non-seed
product still uses. Every product, shipping row and event of a seed vendor goes, including ones
a STAGE tester added by hand to that demo vendor. The VAT rates are kept: they are public facts
the seed only adds where a country has none, and a reset cannot tell them from rates staff
configured. Some rows are legal evidence that the database never deletes, so the reset
retracts them instead:

- contract acceptances are append-only (VEN.5), so the seed vendors are **closed**, not deleted;
  their products and shipping configuration are deleted;
- a legal page is **unpublished**: each publish recorded an append-only version;
- the audit log keeps every entry, including the reset's own.

The demo operator, the demo contract, the default commission rule (rules are never
deleted, VEN.2) and the vendors' tax profiles stay. The next `seed`
reopens the closed vendors (a new onboarding round, VEN.21), accepts the contract again,
activates them and republishes the legal pages.

ARC.22 resets STAGE every hour from a database snapshot taken after `seed`, not with `--reset`.

Vendor E (RF20) shows up in the tax profile consistency report (`inconsistent_tax_profiles`) as a
review note without errors: every RF20 profile is flagged for a manual check (VEN.18). That is
expected, not a seed defect.

## Tests

The `demo_seed` fixture (root `conftest.py`) seeds the test database through the services and
returns the ChangeSet:

```python
def test_something(demo_seed: ChangeSet, client: Client) -> None:
    assert client.get("/shop/vendor-a-workshop/").status_code == 200
```

`apps/seed/tests/` checks the data set (unit), and that the seed runs on an empty database, that
a second run and a dry run change nothing, that `--reset` removes only the seed, and that the
storefront pages show it in EN and IT (integration).

## Timing

About 5 s for a first seed on a developer laptop, 0.2 s for a run with nothing to do, and 1.5 s
for a reset; the ARC.22 budget is 60 s on a 2-vCPU server.
