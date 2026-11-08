# 13 — Data migration and cutover

## 13.1 Strategy

At the current scale (a few vendors, thousands of products per language, dozens of orders per day) the
cheapest solution is a **single cutover, rehearsed several times**, not a prolonged dual-running period.

1. Repeatable, idempotent ETL from a LIVE dump (in a protected environment) to the new schema.
2. Automated parity reports on every run.
3. At least **three dress rehearsals** on STAGE (with anonymized customer data).
4. Short WordPress freeze, final run, DNS switch, smoke test, reopening.
5. WordPress stays **read-only** for 2–4 weeks as a safety net.

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
| Customers | WP users with the customer role | → `User`; WordPress password hashes accepted at first login and then converted | Custom hasher for phpass and for the WP 6.8+ bcrypt format |
| Historical orders | `shop_order` + `wcfm_marketplace_orders` | → `Order`/`VendorOrder`/`OrderLine` **read-only** with a `legacy` flag | No retroactive tax recalculation |
| Open quotes | `_legacy_quote_*` meta | Close before cutover, or migrate their status | Decision in the runbook |
| Vendor ledger | `wcfm_vendor_ledger`, payouts | → opening balance per vendor (one documented `adjustment` row) | Verified against the Stripe reconciliation |
| Reviews | product comments | → reviews with origin | |
| Pages and blog | posts and pages (Elementor) | **Semi-manual** migration: texts extracted, layout rebuilt with StreamField | Elementor HTML does not convert well automatically |
| SEO texts | `_wpsso_meta`, tag and category descriptions | → SEO fields of the translations | |
| Shipping zones | option `custom_shipping_vendors_config_v2` | → `shipping` models (see SHP.5) | |
| Merchant feed | `g:id` = post ID | `legacy_wp_id` used as the id in the new feed | Continuity of Merchant Center history |

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

1. T−7 days: DNS TTL lowered; open quotes closed or scheduled; communication to vendors.
2. T−1: final dress rehearsal; go/no-go on the parity reports.
3. T0: WordPress freeze (maintenance mode, no checkout); final ETL; parity reports.
4. DNS switch; smoke test: EN/IT browsing, search, multi-vendor cart, small real payment
   with refund, quote, vendor dashboard, feeds.
5. Reopening; intensive monitoring for 72 h (404s, errors, conversions, webhooks). Daily 404
   report with any legacy product or tag URL missing from the map fixed the same day (Q-91,
   D-07). Migrated products and taxonomy terms still answer 200 on their legacy URLs.
6. Rollback: DNS back to WordPress (left intact) if a no-go criterion occurs within the first 24 h;
   orders collected on the new system in the meantime are handled manually.
7. T+14/28: Merchant Center and Search Console verified (indexing, redirect and 404 errors,
   ranking review against the pre-cutover baseline), WordPress shut down and archived.
8. From T+14, product and tag URL batches (MIG.11, D-07): one batch a week, lowest traffic first
   (default 15–20% of products or terms); before each batch the gate checks the previous batch's 404 rate, Search
   Console impressions and clicks against baseline, and errors; a batch over threshold is
   rolled back to legacy mode and the schedule pauses until staff review it.

## 13.5 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| MIG.1 | Extractor from the WP DB (`kvsa_`) to an intermediate JSON format, with a meta inventory; per-product URL hit counts from the legacy access logs (traffic fallback for MIG.11) | P1 | 10 | — | Complete inventory of the fields in use; hit counts per legacy product URL exported |
| MIG.2 | Report of duplicate SKUs (same match key, products and variants together), tags without a role, orphan companion products → decisions | P1 | 6 | MIG.1 | Decision list approved |
| MIG.3 | Loader for vendors, catalog, taxonomies, images (idempotent, dry-run); existing IT texts loaded as published translations (D-18) | P1 | 16 | MIG.1, CAT.1, CAT.2, CAT.3, CAT.4, CAT.13, CAT.14 | Reload with no duplicates; IT texts present where the current site has them |
| MIG.4 | Loader for customers (WP password hasher) and read-only historical orders | P1 | 10 | MIG.1, ORD.2 | Migrated customer logs in with the old password |
| MIG.5 | Automated parity reports (§13.3) | P1 | 10 | MIG.3 | Report generated on every run |
| MIG.6 | Complete redirect map in every language + automated check of all WP sitemap URLs; every legacy product URL (SKU permalinks, plain and historical slugs, variant URLs, legacy query-string forms) mapped to the product with its frozen legacy path per language, answering 200 in legacy mode and a single 301 to the SKU-free URL in new mode (Q-91, §13.3); every legacy tag URL (`/tag-product/{slug}/`, `/it/tag-product-it/{slug}-it/`) mapped the same way to its taxonomy term, with a single 301 to `/{taxonomy}/{term}/` in new mode (D-07) | P1 | 8 | CAT.6, MIG.3 | 100% of URLs 200/301; a migrated IT URL serves its IT text, not the EN fallback; on the migration dataset every legacy URL resolves to a 200 page in at most one 301 hop (no chains, no loops), a legacy-mode product or term answers 200 on its old URL with a self-canonical, and the number of legacy product and tag URLs equals the number of mapped URLs |
| MIG.7 | Pages and blog migration (semi-manual) with StreamField; existing IT versions as published wagtail-localize translations (D-18) | P1 | 16 | CMS.2, I18N.3 | All public pages present, in IT too where the current site has them |
| MIG.8 | Vendor ledger opening balances + verification against Stripe | P1 | 4 | VEN.4, MIG.3 | Balance = expected for every vendor |
| MIG.9 | Three dress rehearsals on STAGE + cutover and rollback runbook | P1 | 16 | MIG.5, MIG.6 | Third rehearsal with no blockers |
| MIG.10 | Cutover and post-go-live monitoring, including the daily 404 report, the Search Console review and the product URL batch schedule and gates of the runbook (Q-91) | P1 | 12 | MIG.9, SEC.6 | Go-live, 72 h with no critical issues; 404 report and Search Console review recorded at T+14 and T+28 |
| MIG.11 | Staggered product and tag URL switch (owner decisions Q-91 and D-07): per-product and per-term `url_mode` and frozen legacy paths, traffic ranking from analytics (SEO.5) or the MIG.1 legacy access-log counts, configurable batch size and cadence, batch switch and rollback as a management command and staff action with dry-run and audit, sitemap, canonical and hreflang by mode, gate on 404 rate, Search Console impressions and clicks against baseline, and errors with configurable thresholds | P1 | 6 | MIG.6, CAT.5, CAT.8 | A batch switch gives a single 301 hop and an updated sitemap; a rollback leaves no redirect loop; a legacy-mode page is 200 with a self-canonical; a switched legacy tag URL is a single 301 to `/{taxonomy}/{term}/`; the next batch is refused when a gate metric is over threshold; the MIG.6 "no URL lost" count still holds after a switch and a rollback; products and terms created after go-live start in new mode |
