# 03 — Product catalog

## 3.1 How it works today

| Element | WordPress implementation |
|---|---|
| Types | `simple` and `variable` (`product_variation`); conversion via `convert_to_variable_product` (single axis, e.g. `pa_subscription-duration`, or multiple `pa_taglia` + `pa_colore`) |
| Languages | Two posts per product (EN original, IT translation) linked by WPML `trid`; shared SKU; the IT post sometimes has no attributes (EN fallback) |
| Categories | Hierarchical `product_cat` by product type (Category → Subcategory…), twin EN/IT terms |
| Two-level taxonomy | `product_tag` with term meta `_legacy_tag_role` = level 1 or level 2; level 2 implies level 1; bilingual SEO descriptions |
| Manufacturer brand | `product_brand` (vendors' brands…) → `g:brand` in the feed |
| Attributes | Global `pa_*` attributes (color, material, finish, size, product type, origin, dimensions, capacity, author/ISBN for books…), some with historical typos in the slug |
| Companion product | A `pa_*` attribute holds the **SKU** of a required companion product: a product→product relation encoded as an attribute |
| Key meta | `_sku`, prices, stock, `_weight` and dimensions, `_global_unique_id` (GTIN), `_wcfm_product_author`, `_legacy_old_slugs`, `_wpsso_meta` (Google category), `_upsell_ids` |
| GPSR | **Missing** |

Presentation features from mu-plugins:

- **Slug** `{title}-{lang}-{sku}` (max 80 characters, truncated title, transliteration), regenerated
  on every save and SKU change, `-2` on collisions; last 10 slugs kept in `_legacy_old_slugs` with a 301 on 404s
  (`legacy-sku-permalink.php`, 1,483 lines including the admin console).
- **Redirects**: retired SKU → replacement SKU or tag; recently merged tags; old
  discontinued categories → search; `/author/*` → home; GET `?add-to-cart=` → product page.
- **"Related"**: up to 4 products sharing the most taxonomy terms, excluding upsells
  and hidden products, sorted by number of matches and then randomly.
- **Companion product box** ("You might also need") from a dedicated attribute, with rollout by ID and configurable position.
- **Default variant**: preselects the default one if purchasable, otherwise the cheapest available.
- **Search by SKU**: prefix ≥ 4 characters, including variant SKU → parent, OR-ed with the text search.
- **SKU in cart**, orders and emails (`[SKU] Name`).
- `?s=SKU` links in descriptions rewritten as direct links to the product.
- "On request only" catalog (YITH Catalog Mode, Vendor E): no price and no add-to-cart.

## 3.2 How it will work

### Models

```python
class Product(VendorScopedModel):
    public_id = UUIDField()                   # internal identity (orders, ledger, stock, carts, feeds, URLs, API)
    staff_ref = CharField(unique=True)        # e.g. "P-000123": short prefixed sequence, staff only, never shown to vendors
    vendor = FK(Vendor, on_delete=PROTECT)    # immutable after creation (Q-33)
    sku = CharField(64)                       # vendor label, raw as entered (display, export)
    sku_key = CharField(64)                   # match key; UNIQUE per vendor across products AND variants (Q-30)
    kind = CharField(choices=["simple", "variable"])
    status = CharField(choices=["draft", "published", "retired"])  # retired = page live, not purchasable
    sales_mode = CharField(choices=["buy", "catalog_only", "on_request"])  # Vendor E = on_request
    category = FK(Category)                   # main category (+ secondary M2M)
    brand = FK(Brand, null=True)              # manufacturer
    terms = M2M(Term)                         # navigation taxonomies; upper levels are derived
    fiscal_category = CharField(choices=["standard", "reduced", "book_art74"])
    price_net = MoneyField(null=True)         # net prices (as today: prices_include_tax=no); required for "buy",
                                              # kept but never public for on_request/catalog_only, never 0.00 (Q-31)
    sale_price_net = MoneyField(null=True); sale_from/sale_to
    weight_kg = DecimalField(3)               # mandatory for "buy" (drives shipping)
    length_cm/width_cm/height_cm
    gtin = CharField(null=True)               # GTIN: EAN/UPC/ISBN
    mpn = CharField(null=True)                # manufacturer part number; brand + MPN or GTIN link the same article across vendors (Q-33)
    google_category_id = IntegerField(null=True)
    required_companion = FK("self", null=True)  # formerly a pa_* attribute; same vendor only (Q-32)
    upsells = M2M("self")                     # cross-sell and up-sell; another vendor's product only if that vendor opted in (Q-32)
    gpsr = OneToOne(GpsrInfo, null=True)      # see 11-compliance; blocks publishing if missing
    stock_policy = CharField(choices=["track", "always_open", "from_source"])
    stock_qty; stock_status                   # simple products only; variable products keep stock on their variants (Q-29)
    legacy_wp_id_en / legacy_wp_id_it = IntegerField(null=True)   # Merchant g:id, redirects, audit
    url_mode = CharField(choices=["legacy", "new"])  # migrated products start "legacy"; new products are "new" (Q-91, MIG.11)

class ProductTranslation(Model):              # one row per language, single source of truth for everything else
    product = FK(Product); language = CharField(choices=LANGUAGES)
    title; short_description; description (rich text)
    slug = SlugField()                        # derived: {title}-{lang}, never the SKU
    legacy_path = CharField(null=True)        # frozen legacy URL path of a migrated product (Q-91, MIG.11)
    seo_title; seo_description
    status = CharField(choices=["machine", "reviewed", "published"])  # translation workflow
    source_hash                                # detects when the EN changes → IT "to review"

class Variant(Model):                         # only on variable products (Q-32)
    public_id; product = FK(Product); sku; sku_key (same per-vendor namespace as products); options = M2M(AttributeValue)
    price_net_override; weight_kg_override; stock_qty; stock_status; is_default; position

class Attribute(Model):  code ("color", "material", "size", "bag_width"…); kind (choice|number|text); unit
class AttributeValue(Model): attribute; value; position;  + AttributeValueTranslation(label)
class ProductAttribute(Model): product; attribute; values = M2M; numeric_value; text_value

class Category(LEGACY_Node):          # tree (treebeard), + CategoryTranslation(name, slug, seo, description)
class Taxonomy(Model):           # configurable per vertical; + translation (name, slug)
    code; hierarchical: bool; filterable: bool; has_landing: bool
    # e.g. "compatibility" (brand → model), "occasion", "technique", "season"
class Term(LEGACY_Node):             # tree per taxonomy; + translation (slug, SEO text, legacy_path); url_mode (D-07)
    taxonomy = FK(Taxonomy); attributes (JSON, extra fields defined by the taxonomy)
class Brand(Model):               # manufacturer; + translation

class ProductImage(Model):
    product; variant (null); image = FK(core.MediaImage);   # custom Wagtail image model with licence metadata (CMS.8)
    position; is_cover; cover_locked
    language = LanguageField(null=True)        # null = language neutral (default); set = dedicated to one language
    # cover_locked: a hand-picked cover is not overwritten by the pipelines (current rule)
class ProductImageTranslation(TranslationModel):  # alt_text; caption (EN fallback, I18N.2 workflow)

class SkuAlias(Model):     vendor; product (null); variant (null); sku; sku_key; replaced_at   # old SKUs still match older import files
class SlugHistory(Model):  product; language; slug; replaced_at      # replaces _legacy_old_slugs (unlimited)
class Redirect(Model):     source_path; target (path | product | term | search); code 301/410; expires_at
```

### Main services

```python
# catalog/slugs.py — pure function, replicates legacy-sku-permalink
def build_product_slug(title: str, lang: str, max_len: int = 80) -> str:
    """slugify(translit(title)) truncated + f"-{lang}"; guarantees max_len; never contains the SKU.
    A collision gets a numeric suffix (-2, -3…)."""

# catalog/engine.py: pure functions
def sku_match_key(raw: str) -> str            # NFKC, trim, collapse inner whitespace, casefold
def sku_warnings(raw: str) -> list[str]       # characters that commonly cause trouble; never blocks
def public_price(product) -> Decimal | None    # None for on_request and catalog_only: "Price on request"

# catalog/services.py
def upsert_products(vendor, payload: list[ProductIn], *, dry_run: bool) -> ChangeSet
    # matches by vendor + SKU match key, then SkuAlias; an ambiguous match stops with a report
def duplicate_product_to_vendor(product, vendor, actor, reason, *, dry_run: bool) -> ChangeSet
    # staff only (Q-33): copy as a draft under the new vendor; the original keeps its history
def publish_product(product, actor) -> None        # validates weight, price, GPSR, at least one translation
def retire_product(product, reason) -> None        # stock 0, page stays live (current SEO rule)
def related_products(product, limit=4) -> list[Product]      # match on shared terms, excludes upsells
def default_variant(product) -> Variant | None     # default if purchasable, otherwise cheapest
def resolve_legacy_path(path: str) -> Redirect | None         # retired SKUs, historical slugs, discontinued categories
def search(query: str, lang: str, filters) -> QuerySet        # FTS + trigram + SKU prefix (≥4)

# catalog/engine.py: pure function
def images_for_language(images: list[ImageRow], lang: str) -> list[ImageRow]
    # dedicated images of `lang` if there is at least one, otherwise the neutral images; by position
```

### URLs (per language, prefix `/it/`, `/de/`, `/fr/`, `/es/`)

| Page | EN | IT |
|---|---|---|
| Product | `/product/{slug}/` | `/it/prodotto/{slug}/` |
| Category | `/product-category/{path}/` | `/it/categoria-prodotto/{path}/` |
| Taxonomy (e.g. two levels) | `/{taxonomy}/{term}/` and `/{taxonomy}/{term}/{child}/` | `/it/{tassonomia}/{termine}/…` (translated slugs) |
| Manufacturer brand | `/brand/{slug}/` | `/it/marchio/{slug}/` |
| Vendor storefront | `/shop/{vendor}/` | `/it/shop/{vendor}/` |

Category URLs **stay identical** to the current ones. Product slugs no longer carry the SKU
(owner decision on the vendor SKU, Q-30 and Q-33). The switch is staggered (owner decision Q-91,
[13](13-migration-cutover.md)): at cutover every migrated product keeps answering 200 on its
frozen legacy URL (`url_mode=legacy`); after go-live, batches ordered by traffic move to the new
URL (`url_mode=new`), and the legacy URL becomes a single 301 to it (CAT.5, MIG.6, MIG.11).
Canonical, sitemap and hreflang follow each product's current mode. Products created after
go-live use the new URLs from the start.

Tag and taxonomy pages (owner decision D-07, 2026-11-07) get the descriptive URLs
`/{taxonomy}/{term}/` (`/{taxonomy}/{term}/{child}/` for a second level, translated slugs per
language). The legacy tag URLs `/tag-product/{slug}/` and `/it/tag-product-it/{slug}-it/` move with
the same staggered mechanism as product URLs (MIG.11): at cutover every migrated term keeps
answering 200 on its frozen legacy tag URL with a self-canonical (the term's `url_mode=legacy`,
legacy path stored per term translation); after go-live, batches ordered by traffic switch to
`url_mode=new`, and each legacy tag URL becomes a single 301 to the new term URL, with the same
gate and rollback as products (MIG.6, MIG.11). Canonical, sitemap and hreflang follow each term's
current mode. Terms created after go-live use the new URLs from the start. Pagination and filters
on a taxonomy page build on the term's current URL (CAT.8).

### Rules

- A `buy` product cannot be published without weight > 0, price > 0, at least the EN translation, and GPSR data.
- Changing the title regenerates the slug and writes `SlugHistory`; the old slug answers with a 301.
  Changing the SKU never touches the URL: it writes a `SkuAlias`.
- `retired`: the page stays online with "no longer available" and similar alternatives, never a 404
  (current SEO rule for retiring products).
- Negative stock is impossible (DB constraint); backorders only if `stock_policy=always_open`.
- Stock (owner decision Q-29): a simple product holds its stock on `Product`; a variable product
  holds it on its variants. Cart, orders, stock movements and imports handle both cases.
- Price (owner decision Q-31): `price_net` is required only for `buy` products (database check),
  and it is never 0.00 as a placeholder. An `on_request` or `catalog_only` product keeps its last
  stored price, but the price is never public: not on pages, feeds, JSON-LD or the Merchant feed.
  Buyers see "Price on request"; the vendor and staff see the last price as a reference. When the
  product becomes purchasable again, that price is restored.
- Variants, companions and suggestions (owner decision Q-32): variants exist only on `variable`
  products. A required companion belongs to the same vendor. Suggested products (cross-sell and
  up-sell) may belong to other vendors, but only to vendors that opted in to appear in other
  vendors' suggestions (`CrossVendorConsent.in_suggestions`, a versioned vendor setting with
  audit, [04](04-vendors-commissions-ledger.md), CAT.14). The automatic related products of the product page (CAT.7) are chosen by
  the platform and are not suggestions by a vendor.
- Vendor ownership (owner decision Q-33): products never move between vendors. For a business
  transfer or a mistake, staff duplicate the product as a draft under the new vendor
  (`duplicate_product_to_vendor`, audited); the original keeps its orders, ledger and history and
  can be retired.
- Same article, several vendors (owner decision Q-33): each vendor has its own product record.
  Records are linked by universal identifiers: GTIN, or brand plus MPN. The "other sellers of
  this product" block is P2 (CAT.15); cross-vendor bundles are P2 (CAT.16, CAT.17).

### Cross-vendor bundles (P2, CAT.16 and CAT.17, owner decision)

A bundle is a product of type `bundle` that groups products of several vendors. A vendor's
products can be part of another party's bundle only if that vendor opted in to bundles
(`CrossVendorConsent.in_bundles`, [04](04-vendors-commissions-ledger.md)).

- **Who creates and who funds.** Only staff can define or impose a multi-party funding split
  (several vendors, the marketplace, or both). When a vendor creates a bundle, the whole discount
  is charged to that vendor (100% funded by the creator): it cannot assign shares to other vendors
  or to the marketplace, and the other vendors' lines in its bundle are not discounted.
- **Discount**, set in the bundle's own configuration: a percentage of the bundle list price or a
  fixed amount, with a validity window and optional usage limits.
- **Funding split**, admin tab "Discount funding": one row per participating vendor plus one
  "marketplace" row. Each row is a percentage of the discount or a fixed amount, and the rows add
  up to exactly 100% or the full discount. Validation: the marketplace share allocated to a line is
  at most the commission on that line; a vendor's share is at most that vendor's net on its lines.
  The marketplace share is allocated across lines in proportion to line value (default) or to
  commission (option).
- **Line pricing**: each line's price reduction is the funding attributable to that line, the
  vendor's own share plus the marketplace share allocated to the line. The buyer pays the
  discounted line prices, and each line's VAT base is its discounted price. The marketplace-funded
  part is a reduction of the marketplace's commission, not a payment to the vendor ⚖️ (tax
  advisor: VAT treatment of the marketplace-funded part, Q-90).
- **Settlement**: vendor net = list price − normal commission (snapshot rule, [04](04-vendors-commissions-ledger.md))
  − the vendor's own share; marketplace = commission − marketplace share. The ledger records
  `bundle_discount_vendor_funded` and `bundle_discount_platform_funded` (a commission reduction),
  and the non-fiscal commission statement (CMP.2) shows the reduction. Amounts are `Decimal`,
  `ROUND_HALF_UP` per line; a rounding remainder goes to the largest line.
- **Acceptance of every staff-assigned share** (owner decision): a share that staff assign to a
  vendor always needs that vendor's explicit acceptance; nothing is imposed unilaterally (P2B).
  1. The vendor gets an email through ARC.16 and an entry in the vendor area. The email button
     links to the proposal page in the vendor area, which shows the bundle, the discount, the
     vendor's share in € and %, an example of the vendor's net per unit, the validity window, and
     Accept and Decline buttons (decline with an optional reason).
  2. The link is a CORE.15 secure action link ([02](02-core-identity-permissions.md)): a signed
     token bound to the proposal, the recipient and the proposal version, 72 h by default. A mail
     scanner's GET or HEAD never accepts or consumes it. Without a session the token signs the
     vendor in after a "Continue" POST, without the password, but Accept and Decline are POSTs
     with CSRF after an MFA step-up (CORE.4). A new proposal version invalidates older tokens.
  3. The bundle stays inactive until every funding vendor has accepted. Reminders follow a
     configurable cadence through the CMP.10 reminder mechanism; at the deadline the proposal
     expires and staff are notified. A decline notifies staff, who can revise the split.
  4. Each acceptance or decline is stored with timestamp, actor, proposal version and the exact
     split, and audited. Any change to the discount or the split creates a new version that needs
     a new acceptance.
  5. A vendor-created bundle is 100% funded by its creator and needs no acceptance flow.
- **Orders** snapshot the bundle, the split and the per-line funding, so a later edit never
  changes past orders. The return of one component reverses that line's funding pro rata.

### Product identity and the vendor SKU (owner decisions Q-30 and Q-33)

- The platform identifies products and variants by its own identity: the internal `public_id`
  UUID, plus a staff-only `staff_ref` (a short prefixed sequence) for people. Neither is shown to
  or edited by the vendor. Orders and their snapshots, ledger, stock movements, carts, feeds,
  URLs and API references use the internal identity, never the vendor SKU.
- A product is identified by vendor UUID plus vendor SKU for the vendor, and by its own internal
  UUID for the platform.
- The vendor SKU is a free-text label the vendor uses to recognise the item. Real legacy SKUs
  contain spaces, slashes and other odd characters, and are accepted as they are:
  - the raw SKU is stored exactly as entered, for display and export;
  - a match key is stored next to it: Unicode-normalised (NFKC), trimmed, inner whitespace
    collapsed to one space, case-folded;
  - uniqueness per vendor is on the match key, across products and variants together (Q-30). The
    same SKU is allowed at different vendors;
  - characters that commonly cause trouble (leading or trailing spaces, slashes, quotes,
    control characters, look-alike Unicode) raise a warning to the vendor, never an error;
  - the SKU never appears in URLs, slugs or permalinks.
- Imports (ING) and the bulk upsert API match by vendor plus SKU match key, then by the
  `SkuAlias` history, so a renamed SKU still matches older import files. An ambiguous match (for
  example an old alias of one item equal to the current SKU of another) stops the run with a
  clear report: the importer never guesses.
- Search by SKU (CAT.9) and `?s=SKU` links resolve on the match key within the vendor.
- Images (D-18, spec 09): an image is language neutral unless `language` is set. For each gallery
  (the product's images, or a variant's own images), a language with at least one dedicated image
  shows only its dedicated images; every other language shows the neutral images. The cover is
  chosen within the set that is shown (its `is_cover` image, otherwise the first by position). Feeds
  and JSON-LD use the set of the feed or page language. Alt text and captions live in
  `ProductImageTranslation` and fall back to EN.

## 3.3 Mandatory regression cases

| Case | Input | Expected |
|---|---|---|
| SKU-SPACE | Vendor A creates `AB 100`, then `ab  100 ` | The first is stored raw; the second has the same match key and is refused for vendor A |
| SKU-SLASH | Vendor A creates `BAG/100` | Accepted with a warning; the product URL and slug do not contain the SKU |
| SKU-CASE | Vendor A has product `Bag-100`; a variant `BAG-100` is added | Refused: products and variants share one namespace per vendor |
| SKU-TRIM | Vendor A enters ` BAG-100 ` | Raw kept as entered with a warning; match key `bag-100` |
| SKU-RENAME | `BAG-100` renamed `BAG-100A`, then an import row for `BAG-100` | The import updates the same product through `SkuAlias` |
| SKU-AMBIGUOUS | `BAG-100` renamed `BAG-200`, a new product takes `BAG-100`, then an import row for `BAG-100` | The run stops with a report naming both items; nothing is written |
| SKU-TWO-VENDORS | Vendors A and B both create `BAG-100` | Both accepted; an import for vendor B never touches vendor A |
| BUNDLE-SPLIT | Bundle of vendor A (line €300) and vendor B (line €200), commission 15%, discount 10% (€50) funded A 40%, B 20%, marketplace 40%, marketplace share by line value; amounts before VAT, payment fees left out | A funds €20 and the marketplace €12: line €268, A nets €235. B funds €10 and the marketplace €8: line €182, B nets €160. Buyer pays €450; the marketplace keeps €75 − €20 = €55; 235 + 160 + 55 = 450 |
| BUNDLE-ACCEPT | A staff split assigns shares to vendors A and B; the email link is opened by a mail scanner (GET); B has not accepted; then the split changes | The GET changes nothing; accepting needs login plus POST; an expired token is refused; a token for vendor A's user cannot open vendor B's proposal; accepting without step-up is refused; the bundle stays inactive while B is pending; the changed split invalidates A's earlier acceptance |
| BUNDLE-RETURN | BUNDLE-SPLIT order, the buyer returns vendor B's component | B's line is refunded at €182; B's €10 and the marketplace's €8 funding are reversed pro rata; vendor A's line and ledger rows are unchanged |
| PRICE-ON-REQUEST | A `buy` product priced 120.00 switches to `on_request`, then back to `buy` | Pages, JSON-LD and feeds show no price while on request; vendor and staff see 120.00; back to `buy` the price is 120.00 |

## 3.4 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CAT.1 | Product and Variant models, translations, `(vendor, sku)` constraints | P1 | 12 | ARC.6, VEN.1 | Migrations + constraint tests |
| CAT.2 | Taxonomies: Category (tree), configurable Taxonomy/Term, Brand, translations and SEO texts | P1 | 10 | CAT.1 | Admin CRUD with translations |
| CAT.3 | Typed attributes and translated values; variant axes with ordering | P1 | 10 | CAT.1 | Variable size×color product created via API |
| CAT.4 | Media: ProductImage on Wagtail Images, WebP/AVIF renditions, dedup by hash, cover lock; optional image language with the replacement rule, translatable alt text and captions (D-18) | P1 | 12 | CAT.1, ARC.15, CMS.8 | Upload → automatic renditions; a DE-dedicated image replaces the neutral gallery in DE only; alt text falls back to EN |
| CAT.5 | `build_product_slug` without the SKU + SlugHistory + 301 redirect middleware; current `{title}-{lang}-{sku}` slugs loaded as history | P1 | 8 | CAT.1 | Test cases ported from legacy-sku-permalink without the SKU part; a current SKU slug answers 301 to the new slug |
| CAT.6 | `Redirect` model + admin management + import of existing maps (retired SKUs, merged tags, discontinued categories) | P1 | 6 | CAT.5 | All rules from the 4 mu-plugins covered by tests |
| CAT.7 | Product page: gallery, variants with default, companion box, related products, visible SKU, "on request" | P1 | 16 | CAT.3, CAT.4 | Visual and functional parity with LIVE |
| CAT.8 | Listings: category, taxonomy page on `/{taxonomy}/{term}/` and `/{taxonomy}/{term}/{child}/` with translated slugs (D-07), brand, vendor storefront, filters (category, taxonomy term, price, vendor) | P1 | 14 | CAT.2 | Pagination and filters with indexable URLs; a taxonomy page answers 200 on `/{taxonomy}/{term}/` in every configured language, and its paginated and filtered URLs build on the term's current URL |
| CAT.9 | FTS + trigram + SKU prefix search on the SKU match key (including variants) | P1 | 8 | CAT.1, CAT.13 | Tests on partial SKUs and typos; `bag 100` finds `BAG  100` |
| CAT.10 | `upsert_products`/`publish`/`retire` services with dry-run and publishing rules | P1 | 10 | CAT.1, CMP.3 | ChangeSet verified in tests |
| CAT.13 | Internal identity and the vendor SKU as a label (owner decisions Q-30, Q-33): raw `sku` plus `sku_key` match key on Product and Variant, one per-vendor namespace across products and variants enforced in the database, `SkuAlias` history on rename, non-blocking SKU warnings, staff-only `staff_ref` sequence; drop the upper-case SKU normalisation of CAT.1 | P1 | 8 | CAT.1 | The §3.3 SKU cases are green; a product and a variant of one vendor cannot share a match key; the vendor never sees `staff_ref` |
| CAT.14 | Catalogue integrity rules (owner decisions Q-29, Q-31, Q-32, Q-33): nullable `price_net` with a check (required for `buy`, never 0.00) and `public_price`; `mpn` field; vendor immutable after creation and staff `duplicate_product_to_vendor` as a draft with audit; variants only on variable products; required companion of the same vendor; cross-vendor upsells only to vendors whose versioned `CrossVendorConsent` has `in_suggestions` (vendor setting with audit) | P1 | 8 | CAT.1 | PRICE-ON-REQUEST green; changing a product's vendor is refused and a staff duplicate keeps the original's history; a simple product cannot get a variant; another vendor's companion is refused; an upsell to a vendor without opt-in is refused |
| CAT.15 | "Other sellers of this product" (owner decision Q-33): product page block and API listing the other vendors' published products with the same GTIN, or the same brand and MPN | P2 | 6 | CAT.7, CAT.14 | Two vendors' products with the same GTIN list each other; same MPN with a different brand does not match |
| CAT.16 | Cross-vendor bundles, catalogue side (owner refinement of Q-32): `bundle` product type with components from several vendors, list price, discount (percentage or fixed, validity window, optional usage limits); vendor-created bundles funded 100% by the creator with other vendors' lines undiscounted; staff-only admin tab "Discount funding" (one row per vendor and one marketplace row) with the §3.2 validation; versioned funding proposals; bundle opt-in on `CrossVendorConsent` (versioned, audited) | P2 | 14 | CAT.14 | A vendor cannot assign a share to another vendor or the marketplace; a split that does not add up to 100% or exceeds a line's commission or a vendor's net is refused; a vendor without bundle opt-in cannot be added; a changed discount or split creates a new proposal version |
| CAT.18 | Bundle funding acceptance by vendors: email through ARC.16 and vendor-area entry, proposal page with share in € and %, net per unit example and validity, CORE.15 secure action link bound to proposal, recipient and version, accept or decline by POST with CSRF after an MFA step-up, reminders through the CMP.10 mechanism, expiry and decline notified to staff, acceptance records with version and exact split in the AuditLog | P2 | 12 | CAT.16, CORE.15, CORE.9, CMP.10 | BUNDLE-ACCEPT green; a declined or expired proposal notifies staff and keeps the bundle inactive |
| CAT.17 | Cross-vendor bundles, checkout and settlement: per-line funding and discounted line prices in the cart, one `VendorOrder` per vendor through the multi-vendor split, order snapshot of the bundle, split and per-line funding, ledger kinds `bundle_discount_vendor_funded` and `bundle_discount_platform_funded`, reduction shown on the CMP.2 statement, pro-rata reversal on the return of one component | P2 | 18 | CAT.16, ORD.4, VEN.4, CMP.2 | BUNDLE-SPLIT and BUNDLE-RETURN green; editing a bundle after an order leaves that order's snapshot and ledger unchanged |
| CAT.11 | Catalog admin (Wagtail ViewSet): filters, bulk actions, preview, translation status | P2 | 10 | CORE.8 | Staff edit products without the API |
| CAT.20 | Brand and manufacturer as linked entities (owner, 2026-11-09; SEO.7): `Brand` gains logo (sanitised SVG or raster, THM.12), official URL, `sameAs` links (Wikidata item ID validated by format, Wikipedia per language, official profiles) and an optional `Manufacturer` (legal name, URL, `sameAs`, country); brands are one shared list for all vendors: a vendor picks an existing brand or proposes a new one, which Catalog and moderation approves or merges into an existing brand (duplicate detection on normalised name and Wikidata ID), every change audited; merging two brands moves their products and keeps a redirect from the old brand page | P1 | 8 | CAT.2, CORE.18 | Two vendors selling the same GTIN show the same brand `@id` and `sameAs` in their JSON-LD; a vendor's proposed brand is not public until approved; a merged brand's old URL redirects and its products moved; an invalid Wikidata ID is refused |
| CAT.12 | Audited product edit service (coherence check 5): changes to `fiscal_category`, `sales_mode`, weight and dimensions go through a `catalog.services` function (the owning vendor never changes, Q-33 and CAT.14) with dry-run, ChangeSet and AuditLog; the product and variant admins call it instead of saving directly | P1 | 4 | CAT.1, SEC.4 | A fiscal category change made in the admin shows in the audit view with before and after; a dry run writes nothing; `core.E203` passes without the price-only exemption |
