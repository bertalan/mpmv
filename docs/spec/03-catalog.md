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
    vendor = FK(Vendor, on_delete=PROTECT)
    sku = CharField(64)                       # UNIQUE (vendor, sku); normalized to uppercase
    kind = CharField(choices=["simple", "variable"])
    status = CharField(choices=["draft", "published", "retired"])  # retired = page live, not purchasable
    sales_mode = CharField(choices=["buy", "catalog_only", "on_request"])  # Vendor E = on_request
    category = FK(Category)                   # main category (+ secondary M2M)
    brand = FK(Brand, null=True)              # manufacturer
    terms = M2M(Term)                         # navigation taxonomies; upper levels are derived
    fiscal_category = CharField(choices=["standard", "reduced", "book_art74"])
    price_net = MoneyField()                  # net prices (as today: prices_include_tax=no)
    sale_price_net = MoneyField(null=True); sale_from/sale_to
    weight_kg = DecimalField(3)               # mandatory for "buy" (drives shipping)
    length_cm/width_cm/height_cm
    gtin = CharField(null=True)               # EAN/ISBN; otherwise MPN = SKU
    google_category_id = IntegerField(null=True)
    required_companion = FK("self", null=True)  # formerly a pa_* attribute: required companion product
    upsells = M2M("self")
    gpsr = OneToOne(GpsrInfo, null=True)      # see 11-compliance; blocks publishing if missing
    stock_policy = CharField(choices=["track", "always_open", "from_source"])
    legacy_wp_id_en / legacy_wp_id_it = IntegerField(null=True)   # Merchant g:id, redirects, audit

class ProductTranslation(Model):              # one row per language, single source of truth for everything else
    product = FK(Product); language = CharField(choices=LANGUAGES)
    title; short_description; description (rich text)
    slug = SlugField()                        # derived: {title}-{lang}-{sku}
    seo_title; seo_description
    status = CharField(choices=["machine", "reviewed", "published"])  # translation workflow
    source_hash                                # detects when the EN changes → IT "to review"

class Variant(Model):
    product = FK(Product); sku (UNIQUE per vendor); options = M2M(AttributeValue)
    price_net_override; weight_kg_override; stock_qty; stock_status; is_default; position

class Attribute(Model):  code ("color", "material", "size", "bag_width"…); kind (choice|number|text); unit
class AttributeValue(Model): attribute; value; position;  + AttributeValueTranslation(label)
class ProductAttribute(Model): product; attribute; values = M2M; numeric_value; text_value

class Category(LEGACY_Node):          # tree (treebeard), + CategoryTranslation(name, slug, seo, description)
class Taxonomy(Model):           # configurable per vertical; + translation (name, slug)
    code; hierarchical: bool; filterable: bool; has_landing: bool
    # e.g. "compatibility" (brand → model), "occasion", "technique", "season"
class Term(LEGACY_Node):             # tree per taxonomy; + translation (slug, SEO text)
    taxonomy = FK(Taxonomy); attributes (JSON, extra fields defined by the taxonomy)
class Brand(Model):               # manufacturer; + translation

class ProductImage(Model):
    product; variant (null); image = FK(wagtailimages.Image); position; is_cover; cover_locked
    # cover_locked: a hand-picked cover is not overwritten by the pipelines (current rule)

class SlugHistory(Model):  product; language; slug; replaced_at      # replaces _legacy_old_slugs (unlimited)
class Redirect(Model):     source_path; target (path | product | term | search); code 301/410; expires_at
```

### Main services

```python
# catalog/slugs.py — pure function, replicates legacy-sku-permalink
def build_product_slug(title: str, lang: str, sku: str, max_len: int = 80) -> str:
    """slugify(translit(title)) truncated + f"-{lang}-{sku.lower()}"; guarantees max_len."""

# catalog/services.py
def upsert_products(vendor, payload: list[ProductIn], *, dry_run: bool) -> ChangeSet
def publish_product(product, actor) -> None        # validates weight, price, GPSR, at least one translation
def retire_product(product, reason) -> None        # stock 0, page stays live (current SEO rule)
def related_products(product, limit=4) -> list[Product]      # match on shared terms, excludes upsells
def default_variant(product) -> Variant | None     # default if purchasable, otherwise cheapest
def resolve_legacy_path(path: str) -> Redirect | None         # retired SKUs, historical slugs, discontinued categories
def search(query: str, lang: str, filters) -> QuerySet        # FTS + trigram + SKU prefix (≥4)
```

### URLs (per language, prefix `/it/`, `/de/`)

| Page | EN | IT |
|---|---|---|
| Product | `/product/{slug}/` | `/it/prodotto/{slug}/` |
| Category | `/product-category/{path}/` | `/it/categoria-prodotto/{path}/` |
| Taxonomy (e.g. two levels) | `/{taxonomy}/{term}/` and `/{taxonomy}/{term}/{child}/` | `/it/{tassonomia}/{termine}/…` (translated slugs) |
| Manufacturer brand | `/brand/{slug}/` | `/it/marchio/{slug}/` |
| Vendor storefront | `/shop/{vendor}/` | `/it/shop/{vendor}/` |

Product and category URLs **stay identical** to the current ones, so no redirect is
needed for most of the catalog. The `/tag-product/{slug}/` and
`/it/tag-product-it/{slug}-it/` tags are redirected with a 301 to the new taxonomy pages (decision D-07).

### Rules

- A `buy` product cannot be published without weight > 0, price > 0, at least the EN translation, and GPSR data.
- Changing the SKU or title regenerates the slug and writes `SlugHistory`; the old slug answers with a 301.
- `retired`: the page stays online with "no longer available" and similar alternatives, never a 404
  (current SEO rule for retiring products).
- Negative stock is impossible (DB constraint); backorders only if `stock_policy=always_open`.

## 3.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CAT.1 | Product and Variant models, translations, `(vendor, sku)` constraints | P1 | 12 | ARC.6, VEN.1 | Migrations + constraint tests |
| CAT.2 | Taxonomies: Category (tree), configurable Taxonomy/Term, Brand, translations and SEO texts | P1 | 10 | CAT.1 | Admin CRUD with translations |
| CAT.3 | Typed attributes and translated values; variant axes with ordering | P1 | 10 | CAT.1 | Variable size×color product created via API |
| CAT.4 | Media: ProductImage on Wagtail Images, WebP/AVIF renditions, dedup by hash, cover lock | P1 | 10 | CAT.1, ARC.15 | Upload → automatic renditions |
| CAT.5 | `build_product_slug` + SlugHistory + 301 redirect middleware | P1 | 8 | CAT.1 | Test cases ported from legacy-sku-permalink |
| CAT.6 | `Redirect` model + admin management + import of existing maps (retired SKUs, merged tags, discontinued categories) | P1 | 6 | CAT.5 | All rules from the 4 mu-plugins covered by tests |
| CAT.7 | Product page: gallery, variants with default, companion box, related products, visible SKU, "on request" | P1 | 16 | CAT.3, CAT.4 | Visual and functional parity with LIVE |
| CAT.8 | Listings: category, taxonomy page, brand, vendor storefront, filters (category, taxonomy term, price, vendor) | P1 | 14 | CAT.2 | Pagination and filters with indexable URLs |
| CAT.9 | FTS + trigram + SKU prefix search (including variants) | P1 | 8 | CAT.1 | Tests on partial SKUs and typos |
| CAT.10 | `upsert_products`/`publish`/`retire` services with dry-run and publishing rules | P1 | 10 | CAT.1, CMP.3 | ChangeSet verified in tests |
| CAT.11 | Catalog admin (Wagtail ViewSet): filters, bulk actions, preview, translation status | P2 | 10 | CORE.8 | Staff edit products without the API |
