# 13 — Data migration and cutover

## 13.1 Strategy

At the current scale (a few vendors, thousands of products per language, dozens of orders per day) the
cheapest solution is a **single cutover, rehearsed several times**, not a prolonged dual-running period.

1. Repeatable, idempotent ETL from a LIVE dump (in a protected environment) to the new schema.
2. Automated parity reports on every run.
3. At least **three dress rehearsals** on STAGE (with anonymized customer data).
4. Short WordPress freeze, final run, DNS switch, smoke test, reopening.
5. WordPress stays **read-only** for 2–4 weeks as a safety net. The legacy database goes
   read-only at cutover; an encrypted backup is kept 30 to 60 days, then wiped, with a written
   record (D-16 amended, L-11, adviser opinion 2026-11-11).

## 13.2 What is migrated and how

| Data | WP source | Transformation | Notes |
|---|---|---|---|
| Vendors | WCFM user meta, VTO, Stripe | → `Vendor`, policy translations, `VendorTaxProfile`, `PayoutAccount`, `CommissionRule` | Stripe Connect accounts (`acct_…`) **are reused** if the same platform account is kept: no new KYC onboarding |
| Products | EN + IT posts linked by `trid`, meta, `pa_*` attributes | → one `Product` + EN/IT `ProductTranslation`; variants; typed attributes | Key: vendor + SKU match key + `trid`; raw SKU kept as the vendor label; `legacy_wp_id_en/it` preserved |
| Duplicate SKUs | 29 cases in the pipeline's PostgreSQL, `-2` and triplicates on WP | Report → manual decision (keep / merge / retire) before loading | The per-vendor SKU match key constraint (products and variants together) allows no exceptions |
| Categories | `product_cat` EN/IT | → `Category` + translations; IT products on EN categories fixed by construction | |
| Two-level tags | `product_tag` with `_legacy_tag_role` | → `Taxonomy` / `Term` + SEO texts | Tags without a role must be classified first |
| Companion attribute | SKU of the companion product | → `Product.required_companion` (FK) | Resolved by SKU match key within the same vendor (Q-32); SKUs not found or of another vendor go into a report |
| Brands | `product_brand` | → `Brand` | |
| Images | Media Library | → object storage, dedup by hash, cover and gallery, alt text per language | Renditions regenerated |
| Historical slugs | `_legacy_old_slugs`, redirect mu-plugin maps | → `SlugHistory` + `Redirect` | |
| Customers | WP users with a customer or subscriber role, filtered by the activity rule (MIG.4) | → `User` **without a usable password**; the account is activated when its holder sets a new password through a CORE.15 link (CORE.19) | Owner decision 2026-11-09: no legacy password hash is read, loaded or stored, for this migration and for every connector |
| Historical orders | `shop_order` + `wcfm_marketplace_orders` | → `Order`/`VendorOrder`/`OrderLine` **read-only** with a `legacy` flag | No retroactive tax recalculation. All orders of the last 10 years are migrated; those outside the vendor's MIG.16 choice in `restricted_tax_only`, visible only to the Tax/Audit permission and deleted at 10 years; older orders only anonymised, or not migrated (D-16, L-11) |
| Open quotes | `_legacy_quote_*` meta | Closed before cutover and not migrated (D-20, MIG.9) | Quote tokens are never migrated |
| Vendor ledger | `wcfm_vendor_ledger`, payouts | → opening balance per vendor (one documented `adjustment` row) | Verified against the Stripe reconciliation |
| Reviews | product comments (product reviews are switched off); store reviews in a marketplace table | → reviews with origin, if the vendor chose the data type (MIG.16) | MIG.18 |
| Coupons | coupon posts | Not migrated (D-17, D-39); historical coupon lines stay on the legacy orders | MIG.19 |
| Refunds | refund child orders and marketplace refund requests | → refund data on the legacy order, read-only | MIG.20 |
| Pages and blog | posts and pages (Elementor) | **Semi-manual** migration: texts extracted, layout rebuilt with StreamField | Elementor HTML does not convert well automatically |
| SEO texts | `_wpsso_meta`, tag and category descriptions | → SEO fields of the translations | |
| Shipping zones | option `custom_shipping_vendors_config_v2` | → `shipping` models (see SHP.5) | MIG.17 |
| Merchant feed | `g:id` = post ID | `legacy_wp_id` used as the id in the new feed | Continuity of Merchant Center history |

### Legacy data map (public summary)

Written 2026-11-11 from a read-only code review of the legacy system and the schema of an older
database dump (lane-a, shared with owner approval). It names no host, table prefix, plugin version
or security detail. **It carries no counts.** Counts come from the MIG.1 inventory on a fresh
dump. The only dumps available when this was written are old local clones, so any size is an
order of magnitude. A structure and counts check of the LIVE system is an **open point owned by
lane-a and the owner**; until it is done, treat this map as a hypothesis the MIG.1 inventory must
confirm. Field-level detail stays in the private map.

| Area | Where it lives | Target | Row |
|---|---|---|---|
| Vendors | users with the vendor role; serialized store profile meta (name, slug, logo, address, policies, commission, payout settings); per-language description and policy copies; VAT-regime user meta (`oss` / `domestic_only` / `exempt`, rate, OSS start date) | `Vendor`, `VendorTranslation`, `VendorTaxProfile`, `CommissionRule`; a payout account reference only | MIG.3 |
| Commission | vendor rule, then plan rule, then global rule; frozen per order in the marketplace order meta | per-order snapshot loaded as recorded, never recomputed | MIG.4 |
| Products | `product` and `product_variation` posts, one per language; the EN and IT twins share the SKU | `Product` + translations, `Variant` | MIG.1–3 |
| Companion product | an attribute holding the companion's SKU, plus search links to that SKU in descriptions | `Product.required_companion` | MIG.2–3 |
| Categories, tags, brands | category tree; model and brand tags with a `_legacy_tag_role` term meta (missing on some tags); a brand taxonomy; global attributes, some with typo or duplicate names | `Category`, `Taxonomy`/`Term`, `Brand`, typed attributes | MIG.2–3 |
| Images | attachments; the multilingual plugin keeps one attachment per language for the same file; part of the library was converted to WebP | object storage, dedup by hash, alt text per language | MIG.3 |
| Translations | the multilingual plugin's translation table (group id + language) is the only reliable EN to IT link; a custom language meta written by the import routes can disagree with it | translation rows | MIG.3, MIG.7 |
| Customers | users (customers may hold a generic subscriber role, not a customer role); guest checkout allowed; activity from orders and the e-commerce plugin's "last active" meta (WordPress has no last-login field) | `User` without a usable password, activated through CORE.19 | MIG.4 |
| Orders | e-commerce orders (two storage engines kept in sync; the extractor reads the authoritative one); order items (`line_item`, per-vendor `shipping` with carrier, zone and weight meta, `fee`, `tax`, `coupon`); the marketplace per-vendor order table with commission and refund flags | `Order` / `VendorOrder` / `OrderLine`, read-only, `legacy_wp_order_id`; paid-but-unshipped orders at T0 stay operable | MIG.4 |
| Refunds | refund child orders and the marketplace refund-request table | refund data on the legacy order | MIG.20 |
| Shipping quotes | not a separate entity: an order in a custom `quote-pending` / `quote-ready` status with `_legacy_quote_*` meta (state, revision, per-vendor amounts, hashed pay link, expiry); an older two-order flow left `quote-converted` request orders | open quotes closed before cutover (D-20); quote tokens never migrated | MIG.9 |
| Shipping rates | one option holding the whole v2 configuration: zones (countries, postcode ranges, priority, a world catch-all), carriers per vendor with weight bands and surcharges, pickup options, quote settings; the marketplace plugin's own shipping tables are a derived mirror | `shipping` models | MIG.17, SHP.5 |
| Coupons | coupon posts | not migrated (D-17, D-39); historical coupon lines stay on orders | MIG.19 |
| Reviews | product reviews are switched off; store reviews exist in a marketplace table | reviews with origin, vendor's choice | MIG.18 |
| Pages and blog | pages (part built with a page builder); posts created as classic HTML and linked EN to IT; menus | `StandardPage`, `BlogPostPage`, `LegalPage` (legal pages rebuilt) | MIG.7 |
| Redirects | per-product old slugs (`_legacy_old_slugs`, capped per product and language) plus the core old-slug meta; code maps for retired SKUs (to a replacement product or a model tag), merged model tags (EN and IT), old model categories (to search) and an old brand base; a 404 log table | `SlugHistory`, `Redirect`, frozen legacy paths | MIG.6, MIG.11 |
| Ledger | marketplace ledger (credit and debit per reference: order, withdrawal, withdrawal charges, reverse withdrawal, refund) and withdrawal tables; payouts mostly by split payment at charge time | one `adjustment` opening balance per vendor, verified against the payment provider | MIG.8 |

#### Rules the extractor and loaders follow

- Key products on vendor + SKU match key + translation group id. Never key on SKU alone, because
  EN and IT twins share it.
- Vendor = post author. A marketplace meta duplicates it and can disagree: report mismatches.
- Prices are stored net of VAT. Many marketplace money columns are strings: parse them with
  `Decimal` and reject malformed values.
- Orders fall in two VAT eras. Older orders carry VAT as a per-vendor **fee** line; newer ones use
  native tax lines with a per-vendor tax class. The snapshot and the tax-only archive count both
  as tax (MIG.4).
- Do not trust the marketplace ledger row by row: its maintenance code and an older custom plugin
  rewrote or removed rows. Use a verified opening balance (MIG.8).
- Draft and trashed (retired) products are still redirect sources; their slugs may carry a trash
  suffix.
- Lookup and cache tables (attribute, category and product lookups, the per-store category cache)
  are derived and partly in the wrong language. Never use them as a source.
- Never read password hashes, session tokens, saved payment tokens, payment-provider customer ids
  or vendor bank details.
- Do not rely on a `customer` role or on a single order store.
- Each vendor's MIG.16 choice only decides what is active or visible; the loaders still load
  everything the owner decisions require (all orders of the last 10 years, read-only, in the
  restricted tax-only state; only active customer accounts, activated on demand through CORE.19).

### Source connectors (owner decision 2026-11-09)

Our own migration reads the legacy WordPress database (MIG.1). The owner also wants a hook for
importing from other shops built on known products (WooCommerce, WCFM, later others), used by the
setup wizard (ARC.26) and by a new operator who moves an existing shop onto the platform. Both
paths share one design: a **source connector** reads a source and writes the **intermediate
format**; the loaders (MIG.3, MIG.4 and the following ones) read only the intermediate format and
never know which connector produced it.

```python
class ShopConnector(Protocol):                      # apps/ingest/connectors/base.py
    code: str                                       # "wordpress_db", "woocommerce_rest", "wcfm_rest"
    def capabilities(self) -> ConnectorCapabilities # entities it delivers, fields it cannot give
    def check(self, source: ConnectorSource) -> CheckReport        # reachability, version, permissions
    def extract(self, source: ConnectorSource, entities: set[Entity], *,
                since: datetime | None = None) -> Iterator[ImportRecord]  # read-only

@register_connector("woocommerce_rest")             # registry: also MPMV_IMPORT_CONNECTORS (dotted paths)
class WooCommerceRestConnector: ...
```

- **Intermediate format** `mpmv-import/1`: an evolution of the MIG.1 JSON, with a JSON Schema in
  the repository and Pydantic models. Entities: vendors, categories, attributes and terms, tags,
  products, variants, images (URL and hash), customers, historical orders, reviews, slugs and
  redirects. Every record carries `source` and `source_id`; `fetched_at` is stamped once per run
  in the run manifest, outside the compared output, so two runs on the same source give the same
  records. A field the source cannot give is absent and named in the run report, never guessed.
- **Service and audit.** `ingest.services.import_from_source(source, entities, *, dry_run=True)
  -> ChangeSet` runs a connector and the loaders; it writes one `AuditLog` entry per run (actor,
  source, counts, dry-run or not). An import-origin map keeps `(source, source_id)` → target row,
  so a record seen before is skipped, never updated, and a failed run resumes idempotently.
  Failures shown in the Manager or the API use RFC 9457 problem details.
- **Registry.** A connector is a module that registers itself; Shopify, PrestaShop, Magento or a
  CSV connector are added without touching the loaders or the core.
- **The MIG.1 extractor is the `wordpress_db` connector**, the second transport. It stays the one
  used for our own migration, because the APIs cannot give everything (table below).
- **Rules.** Read-only (connectors send only GET requests to an API, read-only queries to a
  database); dry-run by default and idempotent (the same source twice gives the same output, in a
  stable order); every record validated against the schema before it is written (SEC.9 in
  [12](12-security-infra-operations.md)); secrets only from the environment or a vault, named by
  reference in the source configuration and never stored, logged or written to the output;
  outbound calls through one HTTP client (`apps/core/http.py`) with a per-connector rate limit,
  timeouts, retries with backoff that honour `Retry-After`, HTTPS only, and a log line per call
  (method, path without credentials, status, duration) in the run log. **SSRF guard:** the client
  resolves the host itself and refuses loopback, private (RFC 1918 and IPv6 ULA), link-local,
  CGNAT (100.64.0.0/10) and cloud metadata addresses, connects to the address it checked (no
  second lookup), and refuses a redirect to such an address. The same client serves connectors,
  supplier feeds and any URL entered by staff or vendors. Image files go through the ING.6 pipeline and the
  SEC.2 upload checks. Bank details, KYC documents and payout accounts are never imported: vendors
  enter them again and onboard with the payment provider.
- **Relation to the supplier adapters** of [08](08-import-pipelines.md): a `SupplierAdapter`
  keeps one vendor's catalogue in sync every day (the ING.3 Store API adapter reads a supplier's
  public WooCommerce store); a connector moves a whole shop once, or a few times during a
  migration. They can share the HTTP client and the field mappings.
- **Accounts without passwords** (owner decision 2026-11-09). No connector reads, writes or keeps
  a legacy password hash. Every migrated or imported account is created without a usable
  password and is activated when its holder sets a new one through a CORE.15 link (CORE.19);
  imported vendor members also enrol MFA at first login (CORE.4). The schema has no password
  field.

First connectors and what their APIs cannot give:

| Connector | Reads | Cannot give (stays with the DB extractor or is re-entered) |
|---|---|---|
| `woocommerce_rest` (MIG.13) | WooCommerce REST API v3 with a read-only API key: products, variations, categories, attributes and terms, tags, reviews, images, customers, orders; `per_page=100` pagination with the total-pages header, `modified_after` for incremental runs | WordPress password hashes (never used anyway: every imported account sets a new password, CORE.19), protected meta (keys starting with `_`) and much plugin meta, links between translations unless the multilingual plugin exposes them, SEO plugin fields, slug history and redirects, marketplace ledger |
| `wcfm_rest` (MIG.14) | WCFM Marketplace REST endpoints (`wcfmmp/v1`, an optional add-on found through the `/wp-json` index): store vendors with profile, address, policies and logo, product to vendor link, vendor orders with the commission split | Without the add-on: vendor data and product attribution (fallback: a CSV map of product id to vendor). Even with it: commission rules, ledger and payout history, store SEO; bank data never. The legacy site does not run the add-on, which is one more reason our migration keeps MIG.1 |
| `wordpress_db` (MIG.1) | The WordPress database dump in a protected environment | Nothing of the above is missing (password hashes are not read); used for our own migration only |

## 13.3 Parity reports (automated)

- Counts by vendor, language, category, taxonomy term, status.
- Sample of 100 products compared field by field (price, stock, weight, attributes, images, texts).
- VAT-inclusive price for 50 product × country combinations: WP versus the new tax engine.
- Shipping rates for 200 vendor × country × postcode × weight combinations.
- **All** public EN/IT WP URLs (from the sitemap) → 200 response or 301 to a 200 page.

### Product URLs (owner decision Q-91)

Every migrated product gets a new clean URL without the SKU ([03](03-catalog.md)), and the switch
is **staggered after go-live**:

- **At cutover** every migrated product keeps answering on its legacy URL with a direct 200, not a
  redirect. The frozen legacy path is stored per product and language
  (`ProductTranslation.legacy_path`) with a per-product `url_mode` (`legacy` or `new`). Canonical,
  sitemap and hreflang follow the product's current mode, so a legacy-mode page is
  self-canonical on its legacy URL.
- **The redirect map** (MIG.6) still covers every legacy product URL in every language: SKU
  permalinks, plain and historical slugs (`_legacy_old_slugs`), variant URLs, and the old
  query-string forms the legacy system uses (for example `?p={id}`, `?product={slug}`,
  `?add-to-cart=`). Secondary forms point to the product's current URL, with exactly one hop.
- **Batches by traffic** (MIG.11): lowest-traffic products first, highest-traffic last. Default
  batch about 15–20% of products, one batch per week; both configurable. Traffic comes from the
  analytics data (SEO.5) or, when analytics is not available, from the legacy access-log hit
  counts per product URL taken at migration time (MIG.1).
- **A batch switch** is a management command or a staff action with dry-run (ChangeSet) and an
  `AuditLog` entry. It sets `url_mode=new`, turns the batch's legacy URLs into single-hop 301s to
  the new URLs, and updates sitemaps and canonicals.
- **Gate between batches**: the 404 rate, Search Console impressions and clicks of the switched
  batch against its pre-switch baseline, and the error rate. The next batch goes only when every
  metric is within configurable thresholds.
- **Rollback**: a batch can go back to legacy mode; the legacy URL serves 200 again and the new URL
  answers with a 301 to it. Switching and rollback never create a chain or a loop.
- Each 301 is a single hop to a 200 page; product redirects are permanent by default and never
  removed before 12 months.
- **Accepted risk**: rankings of the switched products may fluctuate for some weeks while search
  engines process the 301s. The owner accepts it; small low-traffic batches first and the gate
  limit the exposure.

### Tag and taxonomy URLs (owner decision D-07)

Tag and taxonomy pages get new descriptive URLs `/{taxonomy}/{term}/` ([03](03-catalog.md)), and
they follow the same staggered switch as product URLs:

- **At cutover** every migrated term keeps answering 200 on its legacy tag URL
  (`/tag-product/{slug}/`, `/it/tag-product-it/{slug}-it/`) with a self-canonical. The frozen
  legacy path is stored per term translation, with a per-term `url_mode`.
- **The redirect map** (MIG.6) covers every legacy tag URL in every language, including tags
  merged by the existing redirect rules (CAT.6), each mapped to its term with exactly one hop.
- **Batches by traffic** (MIG.11): terms go in the same kind of batches, ordered by traffic, with
  the same switch command, gate and rollback as products. A switched legacy tag URL is a single
  301 to the new term URL, never a chain through another tag or product redirect.

## 13.4 Cutover runbook

1. T−7 days: DNS TTL lowered; open quotes closed or scheduled; communication to vendors; a site
   banner announces the change a few days before launch (L-11).
2. T−1: final dress rehearsal; go/no-go on the parity reports.
3. T0: WordPress freeze (maintenance mode, no checkout); final ETL; parity reports. In-flight
   orders (paid, not shipped) are listed per vendor and loaded as operable orders, so vendors ship
   them from the new system; read-only legacy orders cannot be fulfilled (MIG.4, MIG.9).
4. DNS switch; smoke test: EN/IT browsing, search, multi-vendor cart, small real payment
   with refund, quote, vendor dashboard, feeds.
5. Reopening; intensive monitoring for 72 h (404s, errors, conversions, webhooks). Daily 404
   report with any legacy product or tag URL missing from the map fixed the same day (Q-91,
   D-07). Migrated products and taxonomy terms still answer 200 on their legacy URLs.
6. Rollback: DNS back to WordPress (left intact) if a no-go criterion occurs within the first 24 h;
   orders collected on the new system in the meantime are handled manually.
7. T+14/28: Merchant Center and Search Console verified (indexing, redirect and 404 errors,
   ranking review against the pre-cutover baseline), WordPress shut down. The legacy database,
   read-only since T0, survives only as an encrypted backup kept 30 to 60 days, then wiped, with a
   written record (L-11). The updated privacy notice is live from launch, and migrated customers get
   one informative service email, with no link and no promotional content (CORE.19).
8. From T+14, product and tag URL batches (MIG.11, D-07): one batch a week, lowest traffic first
   (default 15–20% of products or terms); before each batch the gate checks the previous batch's 404 rate, Search
   Console impressions and clicks against baseline, and errors; a batch over threshold is
   rolled back to legacy mode and the schedule pauses until staff review it.

## 13.5 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| MIG.1 | Extractor from the WP DB (`kvsa_`) to an intermediate JSON format, with a meta inventory; per-product URL hit counts from the legacy access logs (traffic fallback for MIG.11) | P1 | 10 | — | Complete inventory of the fields in use; hit counts per legacy product URL exported; the inventory reports row counts per entity on a fresh dump (the legacy data map of §13.2 carries no counts); the extractor reads orders from the authoritative order store and reports any disagreement with the other store, and finds customers by activity, not by a single role (see §13.2, "Rules the extractor and loaders should follow") |
| MIG.2 | Report of duplicate SKUs (same match key, products and variants together), tags without a role, orphan companion products → decisions | P1 | 6 | MIG.1 | Decision list approved |
| MIG.3 | Loader for vendors, catalog, taxonomies, images (idempotent, dry-run); existing IT texts loaded as published translations (D-18) | P1 | 16 | MIG.1, CAT.1, CAT.2, CAT.3, CAT.4, CAT.13, CAT.14 | Reload with no duplicates; IT texts present where the current site has them |
| MIG.4 | Loader for customers and read-only historical orders; accounts created without a usable password and activated through CORE.19 (owner decision 2026-11-09: no WordPress password hasher, no legacy hash read or stored); scope per vendor (owner decision D-16, 2026-11-11): the loader migrates only what the vendor chose in the MIG.16 migration choices, a period (last 12, 24 or 36 months, or all) and the data types (orders, the customers of those orders, products, reviews and so on); anything a legal retention duty requires (D-29, D-30) stays whatever the choice and is flagged in the report, never silently dropped; a customer with no order inside the chosen scope is not migrated (DPO note kept: the scope is minimised at the source); amended by D-16 (L-11, owner, 2026-11-11, adviser opinion 2026-11-11): platform and vendors are independent controllers and the MIG.16 choice decides only what is active and visible to the vendor; all orders of the last 10 years are migrated anyway, those outside the choice in `restricted_tax_only` (visible only to the ORD.14 Tax/Audit permission, hidden from the normal UI, deleted by the CMP.29 job at 10 years); older orders are only anonymised, or not migrated; only active customer accounts are migrated (an order in the last 24 months or a login within the window the vendor chose in MIG.16, one of 1, 2, 3, 6, 12, 24 or 36 months, owner, 2026-11-11) | P1 | 8 | MIG.1, ORD.2, CORE.19, MIG.16 | A migrated customer cannot log in until they set a password through the link; no legacy hash is stored anywhere (database, intermediate files, logs); the historical orders are read-only; with a 24-month choice the older orders are left out except those a legal retention duty requires, and those are listed in the report as retained by law; a customer whose only orders are outside the scope is absent from the result; a data type the vendor did not choose is not loaded; the loader refuses to run for a vendor without a recorded MIG.16 choice; with a 24-month choice an order of 5 years ago is migrated in `restricted_tax_only`, absent from the vendor's order list and visible to a Tax/Audit holder; an order older than 10 years is not migrated with personal data; a customer with no order in 24 months and no login within the vendor's chosen window is not migrated; one test per window value (1, 2, 3, 6, 12, 24, 36 months) checks a customer whose last login is just inside the window is migrated and one just outside it is not; a customer is selected by activity (a customer or subscriber role, or none for a guest), never by a single customer role; an order of the earlier VAT era, where VAT is a per-vendor fee line, is loaded with that amount classified as tax in the snapshot and in the restricted tax-only archive, and a test compares the tax total of such an order with the legacy total; money strings are parsed with `Decimal` and a malformed value is rejected with a report; a paid order not yet shipped at T0 is loaded as an operable order that the vendor can fulfil (not read-only), flagged `in_flight_at_cutover`, and the runbook step of MIG.9 lists them |
| MIG.5 | Automated parity reports (§13.3) | P1 | 10 | MIG.3 | Report generated on every run |
| MIG.6 | Complete redirect map in every language + automated check of all WP sitemap URLs; every legacy product URL (SKU permalinks, plain and historical slugs, variant URLs, legacy query-string forms) mapped to the product with its frozen legacy path per language, answering 200 in legacy mode and a single 301 to the SKU-free URL in new mode (Q-91, §13.3); every legacy tag URL (`/tag-product/{slug}/`, `/it/tag-product-it/{slug}-it/`) mapped the same way to its taxonomy term, with a single 301 to `/{taxonomy}/{term}/` in new mode (D-07) | P1 | 8 | CAT.6, MIG.3 | 100% of URLs 200/301; a migrated IT URL serves its IT text, not the EN fallback; on the migration dataset every legacy URL resolves to a 200 page in at most one 301 hop (no chains, no loops), a legacy-mode product or term answers 200 on its old URL with a self-canonical, and the number of legacy product and tag URLs equals the number of mapped URLs; redirect sources from every legacy origin (per-product old slugs, the core old-slug meta, retired-SKU maps, merged model tags in both languages, old model categories and the old brand base) are merged into one map, including draft and trashed products with their trash-suffixed slugs, and each source resolves in a single hop to its final target even when two sources chain |
| MIG.7 | Pages and blog migration (semi-manual) with StreamField; existing IT versions as published wagtail-localize translations (D-18) | P1 | 16 | CMS.2, I18N.3 | All public pages present, in IT too where the current site has them; legacy article HTML converted to the allowed rich-text features through the same sanitiser before `cms.services.import_article` (which stores already structured data, lane-a decision 2026-11-09 on #156), with a parity report listing anything dropped |
| MIG.8 | Vendor ledger opening balances + verification against Stripe | P1 | 4 | VEN.4, MIG.3 | Balance = expected for every vendor; each opening balance is verified against the payment provider's own records and is never rebuilt from the legacy ledger rows (which were rewritten and removed by maintenance code); a vendor whose balance does not match the provider is listed and blocks the cutover |
| MIG.9 | Three dress rehearsals on STAGE + cutover and rollback runbook; open quotes (owner decision D-20, 2026-11-11): open quotes are closed before the cutover, with a 7-day freeze on new quote requests starting at T-7 days (the runbook step "open quotes closed" of §13.4), and none is migrated; legacy data at cutover (L-11, adviser opinion 2026-11-11): the legacy database goes read-only at T0, an encrypted backup is kept 30 to 60 days and then wiped, with a written record; the privacy notice is updated at launch and a site banner runs a few days before | P1 | 16 | MIG.5, MIG.6 | Third rehearsal with no blockers; the runbook has the T-7 freeze step and its check; at the cutover rehearsal no quote is open and a new quote request in the freeze window is refused with a message; no open quote appears in the migrated data; the runbook has the read-only, encrypted-backup and wipe-with-record steps with their dates and the banner and privacy-notice steps; the runbook has an in-flight orders step: the paid-but-unshipped orders at T0 are listed per vendor, loaded by MIG.4 as operable, and shipped from the new system, with a rehearsal check that none is left read-only |
| MIG.10 | Cutover and post-go-live monitoring, including the daily 404 report, the Search Console review and the product URL batch schedule and gates of the runbook (Q-91) | P1 | 12 | MIG.9, SEC.6 | Go-live, 72 h with no critical issues; 404 report and Search Console review recorded at T+14 and T+28 |
| MIG.11 | Staggered product and tag URL switch (owner decisions Q-91 and D-07): per-product and per-term `url_mode` and frozen legacy paths, traffic ranking from analytics (SEO.5) or the MIG.1 legacy access-log counts, configurable batch size and cadence, batch switch and rollback as a management command and staff action with dry-run and audit, sitemap, canonical and hreflang by mode, gate on 404 rate, Search Console impressions and clicks against baseline, and errors with configurable thresholds | P1 | 6 | MIG.6, CAT.5, CAT.8 | A batch switch gives a single 301 hop and an updated sitemap; a rollback leaves no redirect loop; a legacy-mode page is 200 with a self-canonical; a switched legacy tag URL is a single 301 to `/{taxonomy}/{term}/`; the next batch is refused when a gate metric is over threshold; the MIG.6 "no URL lost" count still holds after a switch and a rollback; products and terms created after go-live start in new mode |
| MIG.12 | Source-connector interface (owner decision 2026-11-09, spec 13 §13.2 "Source connectors"): `ShopConnector` protocol (`capabilities`, `check`, `extract`), registry by decorator and `MPMV_IMPORT_CONNECTORS`, intermediate format `mpmv-import/1` (JSON Schema and Pydantic models, an evolution of the MIG.1 JSON) read by the MIG.3 and MIG.4 loaders only; shared HTTP client `apps/core/http.py` (HTTPS only, per-connector rate limit, timeouts, backoff honouring `Retry-After`, one log line per call without credentials, SSRF guard refusing loopback, private, link-local, CGNAT and metadata addresses and redirects to them); `ingest.services.import_from_source(..., dry_run=True) -> ChangeSet` with one AuditLog entry per run and an import-origin map on `(source, source_id)`; `fetched_at` in the run manifest; RFC 9457 errors; secrets by reference from the environment or a vault; schema validation of every record (SEC.9); read-only, dry-run by default, idempotent; the MIG.1 extractor wrapped as the `wordpress_db` connector; connectors import no app models (import-linter contract) | P1 | 9 | MIG.1 | A fake connector registered from settings appears in the registry and the loaders load its output with no change to their code; a record that fails the schema is rejected with a report and nothing of it is written; a canary secret never appears in logs, output or the database; two runs on the same source give byte-identical records (the manifest aside); a source resolving to 127.0.0.1, 10.x, 169.254.x, 100.64.x or a private IPv6 address is refused, also when reached by a redirect; a run interrupted halfway resumes without duplicates; a dry run writes nothing and returns the ChangeSet; the MIG.1 output validates against `mpmv-import/1`; a connector that sends a non-GET request fails its test |
| MIG.13 | WooCommerce REST API v3 connector `woocommerce_rest` (owner decision 2026-11-09): read-only consumer key and secret from the environment, HTTPS only; products, variations, categories, attributes and terms, tags, reviews, images, customers, orders; pagination with `per_page=100` and the total-pages header, `modified_after` for incremental runs; capabilities list the gaps (no password hashes, no protected meta, translation links only when the multilingual plugin exposes them, no SEO plugin fields, no slug history, no ledger); CI runs against recorded responses of a test shop, never a live shop | P2 | 12 | MIG.12 | On the recorded fixture every entity comes out schema-valid, across at least three pages; a 429 response is retried after `Retry-After`; missing credentials give an error that names the environment variable, never a value; customers carry no password field and are created without a usable password; an imported customer activates the account through the CORE.19 link; an `http://` source URL is refused |
| MIG.14 | WCFM Marketplace connector `wcfm_rest` (owner decision 2026-11-09): discovers the `wcfmmp/v1` namespace from the `/wp-json` index; store vendors (profile, address, policies, logo), product to vendor link, vendor orders with the commission split; without the add-on, a run report of the gaps and a CSV map of product id to vendor as fallback; never bank, KYC or payout data; imported vendor members created without a usable password, activated through CORE.19 and enrolling MFA at first login (CORE.4) | P2 | 8 | MIG.13, CORE.19 | A recorded fixture with the add-on gives vendors and product attribution; a fixture without it completes with the gap report and attributes products from the CSV map; the schema has no bank, KYC or payout field, so no such data can be written; an imported vendor member sets a password and enrols MFA before any Manager page |
| MIG.15 | Audit of the migration article import (accepted by lane-a 2026-11-10): cms `import_article` writes no `AuditLog` today and relies on Wagtail page history; decide whether the migration import needs its own audit trail, and if so implement it (one entry per imported article with the source id and the run id; owner: MIG lane) | P2 | 3 | MIG.2, SEC.4 | The decision is recorded in this spec; if audited, a migration import run writes one `AuditLog` entry per imported article and a re-run writes none for unchanged articles |
| MIG.16 | Vendor "migration choices" step (owner decision D-16, 2026-11-11): before the dry run of its migration each vendor records, in a Manager form in the CORE.17 shop space, what to migrate: the period (last 12, 24 or 36 months, or all) and the data types (orders, the customers of those orders, products, reviews and so on); the form shows what the legal retention floor forces to stay (D-29, D-30) and what it means for customers with no order in scope (not migrated); the choice is stored per vendor and per run, versioned, and every change writes an `AuditLog` entry with the actor and the previous value; the dry run (MIG.3, MIG.4) is blocked until a choice is confirmed and its report lists, per data type, what is in and out of scope and what stays because of the floor; staff see the choices in the platform space and can enter them on a vendor's behalf with audit; the DPO note of D-16 stays; amended by D-16 (L-11, adviser opinion 2026-11-11): the choice decides only what is active and visible to the vendor; the form states that the orders of the last 10 years are migrated anyway in the restricted tax-only state; the vendor also chooses the recent-login window for its customer accounts (MIG.4) from 1, 2, 3, 6, 12, 24 or 36 months (owner, 2026-11-11), audited like the rest of the choice | P1 | 6 | MIG.12, CORE.17 | A vendor member records a choice for its own vendor only (another vendor's choice answers 404 and is audited); the form states the legal floor; without a confirmed choice the dry run refuses to start; a change writes one `AuditLog` entry with the old and new value; the dry run report matches the choice and lists the floor items; an e2e test with screenshots of the form; the form shows the restricted tax-only archive text; the login-window field offers exactly 1, 2, 3, 6, 12, 24 and 36 months, each value saved and read back by a test, and any other value is refused |
| MIG.17 | Shipping configuration migration: the MIG.1 extractor exports the legacy shipping option (zones with countries, postcode ranges and priority, the world catch-all, carriers per vendor with weight bands and surcharges, pickup options, quote settings) to the intermediate format, and the SHP.5 loader writes the `shipping` models; the marketplace plugin's own shipping tables are treated as a derived mirror and never used as a source | P1 | 4 | MIG.12, SHP.5 | A fresh-dump run loads the configuration with no duplicates on reload; the §13.3 shipping parity (200 vendor × country × postcode × weight combinations) shows no difference; the world catch-all and every pickup option are present; a value that does not parse is reported, never defaulted |
| MIG.18 | Reviews migration: store reviews (marketplace table) and any product comments are loaded as reviews with an origin flag, only when the vendor chose the data type in MIG.16; product reviews are switched off on the legacy shop, so they stay hidden until the owner decides otherwise | P2 | 3 | MIG.3, MIG.16 | A vendor that did not choose reviews gets none loaded; a chosen review keeps its author display name, rating, date and origin; reload creates no duplicate; no reviewer personal data beyond the display name is loaded |
| MIG.19 | Coupons are not migrated (D-17; coupons as a feature belong to D-39, issue #243 and the promotions spec 18, still under review): the extractor reports the coupon posts that exist, and the historical coupon lines of migrated orders are kept as read-only order lines | P2 | 1 | MIG.4 | A migrated order with a coupon line shows it unchanged and read-only; the run report lists the unmigrated coupons; no coupon is created in the new system by the migration |
| MIG.20 | Refunds migration: refund child orders and marketplace refund requests are attached to the legacy order they belong to, as read-only refund data (amount, date, reason, flag), with no new ledger entry and no tax recalculation | P1 | 4 | MIG.4 | A migrated order shows its refunds; the sum of an order's refunds equals the legacy refund total; a refund amount that does not parse as `Decimal` is rejected with a report; the migration writes no ledger entry for a refund (MIG.8 covers balances) |
