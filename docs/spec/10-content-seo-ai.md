# 10 — Content (Wagtail), SEO, feeds and AI channels

## 10.1 How it works today

- Pages and blog in WordPress with Elementor (`storefront` theme + custom child theme); "Become a Vendor",
  "Vendor Membership" and affiliate pages; bilingual blog with `scripts/blog/` scripts.
- **WPSSO Core Premium** + add-ons: JSON-LD Product/Offer/Breadcrumb/MerchantReturnPolicy/
  ShippingDetails/AggregateRating, Open Graph, sitemap, Google Merchant and Meta feeds.
- SEO mu-plugin: schema.org relations between products and terms (`isRelatedTo` and similar) in the JSON-LD; robots
  `max-snippet:-1`; noindex on 404s; internal search indexable only when it has results; author redirects;
  IT breadcrumb fix.
- `google-feed.php`: EN/IT RSS feed with 6 h cache, **fixed 22%** VAT, hardcoded shipping,
  `g:id` = **WordPress post ID**, Google category from `_wpsso_meta` or by vendor (per-vendor override, generic default),
  GTIN or MPN = SKU.
- `scripts/google_merchant_api/`: sync via the Merchant API (EN+IT data source, delta reports).
- Static `llms.txt` (48 lines). Analytics with Site Kit and Google for WooCommerce; cookies with Complianz.
- Internal MCP roadmap: server-side MCP for admin and vendors, then client-side WebMCP.

## 10.2 How it will work

### Wagtail pages

| Page type | Use | StreamField blocks |
|---|---|---|
| `HomePage` | Home | hero, product grid (query), featured terms, featured vendors, CTA |
| `StandardPage` | About us, contact, FAQ | rich text, images, FAQ (with FAQPage schema), CTA |
| `LegalPage` | Terms, privacy, cookies, withdrawal, returns | versioned text with effective date |
| `BlogIndexPage` / `BlogPostPage` | Bilingual articles, featured vendors | text, galleries, **product/term/vendor choosers** (no hand-typed URLs), author, scheduling |
| `TermLandingPage` | Editorial page for a taxonomy term | SEO intro, the term's products by category, related articles |
| `VendorLandingPage` | The vendor's story (heritage, workshop) beyond the standard vendor storefront | story, images, featured products |
| `VendorRecruitmentPage` | "Become a seller" with plans and application form | plans (from `VendorPlan`), FAQ, form |

Taxonomy and vendor pages combine editorial content with catalog data: they are the SEO strong point
("browse by taxonomy term") and today they are fragmented across tags, categories and articles.

### Images per locale (D-18)

Images are language neutral by default (spec 09 §9.2). With wagtail-localize (I18N.3), image fields
on page models are declared overridable (`SynchronizedField(..., overridable=True)`) and image
chooser blocks in StreamField stay overridable segments, so a translator can replace an image for
one locale while the other locales keep the source image. Alt text and captions are translatable
segments with EN fallback. A gallery block marks each image as neutral or dedicated to one language
and applies the same replacement rule as product images: a language with at least one dedicated
image shows only those, otherwise the neutral ones.

### Technical SEO (`seo` app)

```python
def product_jsonld(product, lang) -> dict
    # Product + Offer (VAT-inclusive price for the default country) + brand + gtin/mpn
    # + isRelatedTo (and sector-specific properties, if configured) + shippingDetails + hasMerchantReturnPolicy
    # + aggregateRating (verified reviews only)
def breadcrumb_jsonld(obj, lang) -> dict
def robots_meta(request, obj) -> str            # noindex on 404, empty search, pagination beyond a threshold
def sitemaps() -> dict[str, Sitemap]           # per language and type; excludes fallbacks
def llms_txt(lang) -> str                       # generated from vendors, brands and taxonomy terms, key pages
```

### Feeds and Merchant

```python
def merchant_items(lang, country) -> Iterable[MerchantItem]
    # id = legacy_wp_id if present (continuity of Merchant history), otherwise public_id
    # VAT-inclusive price computed with the tax engine for the feed's country (not a fixed 22%)
    # shipping per country and band from the shipping tables (not hardcoded)
    # Google category: product → category → vendor → default
def sync_merchant(lang) -> SyncReport            # Merchant API, delta, no mass deletion
def meta_catalog_feed(lang) -> Response          # Meta Commerce feed
```

### Analytics and consent

- Consent banner with Consent Mode v2 (certified CMP or self-hosted solution, D-10).
- Standard e-commerce events (view_item, add_to_cart, begin_checkout, purchase), also server-side
  after consent; minimal internal dashboard (conversion, sales per vendor).

### Reviews

- Product reviews from **verified purchases** + reviews imported from suppliers (with declared
  origin and original date, as today). Staff moderation, vendor reply.
- Review transparency (Omnibus directive): explain how reviews are verified.

### AI channels

1. **MCP server for management** (P2): thin adapter over the services, authenticated as a scoped
   `ApiClient`.
   - Read tools: search products, translation status, simulate VAT or shipping, import reports,
     late orders, open quotes.
   - Mutating tools only with dry-run and diff; execution after human confirmation; every call audited.
   - Replaces the `diagnose_*` / `fix_*` scripts and the current "AI + API route + ad hoc script" flow.
   - Product and feed texts are **untrusted input** (prompt injection): never execute instructions
     contained in the data.
2. **Agent-readable catalog** (P2): generated `llms.txt`, complete JSON-LD, up-to-date feeds,
   read-only public catalog API (search by taxonomy term).
3. **Agentic commerce** (P3): protocols such as the Agentic Commerce Protocol (Stripe and OpenAI) or
   browser-side WebMCP. To be evaluated once the protocols stabilize. The service architecture and
   the use of Stripe make them one more adapter, not a rewrite.

### Performance

Anonymous page cache (invalidated on publish), responsive images (WebP/AVIF, `srcset`),
minimal CSS and JS, Core Web Vitals budget defined before the frontend (LCP < 2.5 s on mobile 4G).

### Frontend theme

The public frontend is rendered by themes: a vertical-neutral `base` theme plus vertical themes,
with design tokens, template overrides, CSP-safe components and WCAG 2.2 AA rules. See
[14](14-frontend-themes.md); the former CMS.7 row is split into THM.1 to THM.10 there.

## 10.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CMS.1 | Wagtail: site, locales, base page types, per-language navigation and footer | P1 | 8 | ARC.3 | EN/IT home and static pages |
| CMS.2 | Reusable StreamField blocks (hero, product grid, CTA, FAQ, choosers) | P1 | 10 | CMS.1, CAT.8 | An editor builds the home page without code |
| CMS.3 | Blog with scheduling, author, product/term/vendor chooser | P1 | 8 | CMS.2 | Migrated article published |
| CMS.4 | TermLandingPage and VendorLandingPage | P1 | 10 | CMS.2, CAT.8 | A term page with its products |
| CMS.5 | Versioned LegalPage + VendorRecruitmentPage with application form | P1 | 6 | CMS.1, VEN.5 | Application arrives in admin |
| CMS.6 | Reviews: model, purchase verification, supplier import, moderation, vendor reply | P2 | 10 | ORD.2 | AggregateRating schema only with valid reviews |
| SEO.1 | JSON-LD Product/Offer/Breadcrumb/ReturnPolicy/ShippingDetails, robots, canonical | P1 | 10 | CAT.7, SHP.3 | Rich Results Test with no errors |
| SEO.2 | Per-language and per-type sitemaps, generated `llms.txt` | P1 | 4 | I18N.6 | Valid sitemaps |
| SEO.3 | Google feed (EN/IT) with legacy IDs, prices from the tax engine, shipping from tables | P1 | 8 | TAX.3, SHP.11 | Feed validated in STAGE Merchant Center |
| SEO.4 | Merchant API sync + Meta feed | P2 | 8 | SEO.3 | Daily delta report |
| SEO.5 | Cookie consent + Consent Mode v2 + e-commerce events | P1 | 8 | ORD.5 | No tags fire before consent |
| SEO.6 | Anonymous page cache, responsive renditions, CWV budget | P2 | 6 | THM.3 | LCP < 2.5 s on product page |
| AI.1 | MCP server: auth, read tools (catalog, orders, quotes, simulations, reports) | P2 | 12 | CORE.7 | Tools usable from Claude Code with scopes |
| AI.2 | Mutating MCP tools with dry-run, confirmation and audit | P2 | 10 | AI.1 | Every mutation has a diff and an audit entry |
| AI.3 | Read-only public catalog API for agents | P3 | 6 | CAT.9 | Search by taxonomy term via API |
| AI.4 | Evaluation of agentic commerce protocols (ACP, WebMCP) and prototype | P3 | 12 | PAY.1, AI.3 | ADR with recommendation |
