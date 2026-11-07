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

## 13.4 Cutover runbook

1. T−7 days: DNS TTL lowered; open quotes closed or scheduled; communication to vendors.
2. T−1: final dress rehearsal; go/no-go on the parity reports.
3. T0: WordPress freeze (maintenance mode, no checkout); final ETL; parity reports.
4. DNS switch; smoke test: EN/IT browsing, search, multi-vendor cart, small real payment
   with refund, quote, vendor dashboard, feeds.
5. Reopening; intensive monitoring for 72 h (404s, errors, conversions, webhooks).
6. Rollback: DNS back to WordPress (left intact) if a no-go criterion occurs within the first 24 h;
   orders collected on the new system in the meantime are handled manually.
7. T+14/28: Merchant Center and Search Console verified, WordPress shut down and archived.

## 13.5 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| MIG.1 | Extractor from the WP DB (`kvsa_`) to an intermediate JSON format, with a meta inventory | P1 | 10 | — | Complete inventory of the fields in use |
| MIG.2 | Report of duplicate SKUs (same match key, products and variants together), tags without a role, orphan companion products → decisions | P1 | 6 | MIG.1 | Decision list approved |
| MIG.3 | Loader for vendors, catalog, taxonomies, images (idempotent, dry-run); existing IT texts loaded as published translations (D-18) | P1 | 16 | MIG.1, CAT.1, CAT.2, CAT.3, CAT.4, CAT.13, CAT.14 | Reload with no duplicates; IT texts present where the current site has them |
| MIG.4 | Loader for customers (WP password hasher) and read-only historical orders | P1 | 10 | MIG.1, ORD.2 | Migrated customer logs in with the old password |
| MIG.5 | Automated parity reports (§13.3) | P1 | 10 | MIG.3 | Report generated on every run |
| MIG.6 | Complete EN/IT redirect map + automated check of all WP sitemap URLs | P1 | 6 | CAT.6, MIG.3 | 100% of URLs 200/301; a migrated IT URL serves its IT text, not the EN fallback |
| MIG.7 | Pages and blog migration (semi-manual) with StreamField; existing IT versions as published wagtail-localize translations (D-18) | P1 | 16 | CMS.2, I18N.3 | All public pages present, in IT too where the current site has them |
| MIG.8 | Vendor ledger opening balances + verification against Stripe | P1 | 4 | VEN.4, MIG.3 | Balance = expected for every vendor |
| MIG.9 | Three dress rehearsals on STAGE + cutover and rollback runbook | P1 | 16 | MIG.5, MIG.6 | Third rehearsal with no blockers |
| MIG.10 | Cutover and post-go-live monitoring | P1 | 12 | MIG.9, SEC.6 | Go-live, 72 h with no critical issues |
