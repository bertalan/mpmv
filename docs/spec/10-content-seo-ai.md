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
| `HomePage` | Home | hero, product grid (query, CMS.10), featured terms, featured vendors, CTA |
| `StandardPage` | About us, contact, FAQ | rich text, images, FAQ (with FAQPage schema), CTA |
| `LegalPage` | Terms, privacy, cookies, withdrawal, returns | versioned text with effective date, one version history per language (Q-42, I18N.9) |
| `BlogIndexPage` / `BlogPostPage` | Bilingual articles, featured vendors | text, galleries, **product/term/vendor choosers** (no hand-typed URLs), author, scheduling |
| `TermLandingPage` | Editorial page for a taxonomy term | SEO intro, the term's products by category, related articles |
| `VendorLandingPage` | The vendor's story (heritage, workshop) beyond the standard vendor storefront | story, images, featured products |
| `VendorRecruitmentPage` | "Sell with us" with plans and the link to the application (VEN.22) | plans (from `VendorPlan`), FAQ, call to action |
| `EventIndexPage` / `EventPage` | Events of vendors and of the platform: promotions, trade fairs and meetings, other events, in any enabled language, with RSS and ICS feeds and maps ([15](15-events.md), `events` app) | the block library above, the launch countdown, structured dates, recurrence, location and coordinates |

Events have their own page type, `EventPage`, in the `events` app ([15](15-events.md)). This
supersedes owner decision Q-61 for events, which had made them blog articles. There are still no
page types for tours, trails or packages: they are product categories (CAT.2) or article content.
The launch countdown block below is also available in `EventPage` bodies.

Taxonomy and vendor pages combine editorial content with catalog data: they are the SEO strong point
("browse by taxonomy term") and today they are fragmented across tags, categories and articles.
Taxonomy pages live on `/{taxonomy}/{term}/` (owner decision D-07, [03](03-catalog.md)); the
legacy `/tag-product/{slug}/` URLs answer 200 with a self-canonical at cutover and then move to
the new URLs with single-hop 301s in traffic-ordered batches (MIG.11).

The product grid block (CMS.10) lists products by category, taxonomy term, brand or vendor with
the CAT.8 listing query, so a grid and the matching listing page show the same products. It is a
separate row from the base block library (CMS.2), which does not wait for the catalog listings.
The editor picks one category, term, brand or vendor in a chooser and a limit from 1 to 24 (one
listing page); the grid shows the newest products as the listing's product cards and, unless the
editor turns it off, a "See all" link to the canonical listing page in the page's language. It is
offered in `HomePage` and `StandardPage` bodies; a grid with no public product is left out.

### Launch countdown block (CMS.9)

A StreamField block that announces a product launch, available in `HomePage`, `StandardPage`,
`BlogPostPage` and `EventPage` bodies ([15](15-events.md)).

| Field | Rule |
|---|---|
| `target` | Date and time of the launch, entered in the operator's time zone, stored in UTC |
| `heading`, `body` | Translatable text |
| `product` or `category` | Optional chooser, at most one of the two |
| `cta_label`, `cta_link` | CTA before the launch; the link defaults to the chosen product or category |
| `expired_text`, `expired_link` | Shown from the target on (default "Available now"); the link defaults to the product or category |

- **Server-rendered fallback**: the block renders the absolute date as
  `<time datetime="…">` text with the time zone name (for example "1 December 2026, 10:00 CET")
  and the CTA. It is complete and readable without JavaScript. From the target on, the server
  renders the expired state. A pure `countdown_state(target, now)` decides the state, and the
  anonymous page cache (SEO.6) never keeps a page with an active countdown past its target.
- **Progressive enhancement, CSP-safe**: the markup carries `data-countdown` and
  `data-target="<ISO 8601 UTC>"`; an external ES module of the theme (§14.5 of
  [14](14-frontend-themes.md)) adds the remaining days, hours and minutes and rewrites the absolute
  date in the visitor's time zone with `Intl.DateTimeFormat` in the page language. No inline
  script, no `style` attribute. The expired content is rendered server-side with `hidden` and
  revealed by the module at the target.
- **Accessibility (WCAG 2.2 AA)**: the counter is not an `aria-live` region and is never announced
  on each update; only the switch to the expired state is announced once, through the page's
  polite live region (4.1.3). Updates happen once a minute (no seconds), and a visible pause
  button stops them (2.2.2). No animation under `prefers-reduced-motion: reduce` (2.3.3). The
  remaining time is always shown next to the absolute date, so the information does not depend on
  the update.

### Image source, licence and credit (CMS.8)

Every image comes from a free-licence source that allows commercial use or is self-produced
(owner decision Q-63). Wagtail uses a custom image model, `MediaImage` (`AbstractImage`) in
`apps/core`, set as `WAGTAILIMAGES_IMAGE_MODEL` before any image exists, so CMS pages, product
images (CAT.4), vendor logos and banners share it. CMS.8 does not depend on ARC.15: the image
model and its validation work with local storage, and ARC.15 only moves the storage backend to
object storage later:

| Field | Rule |
|---|---|
| `licence` | Required, from the staff-editable `ImageLicence` allow list (audited). Seeded: `own-work` (operator), `vendor-supplied` (a vendor or its supplier feed, under the vendor contract), `written-permission`, `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`. Each entry has a licence URL and an `attribution_required` flag; non-commercial (NC) and no-derivatives (ND) licences are never allowed |
| `source_url` | Required except for `own-work` and `vendor-supplied` |
| `author` | Required: the author or rights holder to credit |
| `credit_text` | Optional override of the generated credit line |
| `permission_ref` | Required for `written-permission`: reference to the stored permission document |

- An upload, in the Wagtail admin, the vendor area or a service, without the required fields is
  refused. Supplier imports (ING) set `vendor-supplied`, the vendor as author and the product URL
  as source. Images migrated from the legacy site without known metadata get a staff review list
  and cannot be chosen for new content until they are classified.
- Where the licence requires attribution, the `image_credit` component renders the credit
  (author, licence with link, source) in the image's `<figcaption>`. Images with no caption area
  (hero, banners, product cards) are credited in an "Image credits" disclosure at the end of the
  page.

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
    # gtin and mpn from the product fields, never the vendor SKU (Q-33); no price in the Offer
    # for on_request and catalog_only products (Q-31)
    # + isRelatedTo (and sector-specific properties, if configured) + shippingDetails + hasMerchantReturnPolicy
    # + aggregateRating (verified reviews only)
def breadcrumb_jsonld(obj, lang) -> dict
def robots_meta(request, obj) -> str            # noindex on 404, empty search, pagination beyond a threshold
def sitemaps() -> dict[str, Sitemap]           # per language and type; excludes fallbacks;
    # products and taxonomy terms listed on the URL of their current mode (Q-91, D-07, MIG.11)
def llms_txt(lang) -> str                       # generated from vendors, brands and taxonomy terms, key pages, event feeds
def event_jsonld(page, lang) -> dict | None     # schema.org Event with Place, PostalAddress, GeoCoordinates (spec 15, EVT.10)
```

### Structured data graph (SEO.7)

Every public page carries ONE JSON-LD block (`<script type="application/ld+json">`) holding a
schema.org `@graph`, built by one builder in the `seo` app (`page_graph(request, obj, lang)`).
The per-type functions above return graph nodes, not separate scripts. Nodes have stable `@id`s
(the canonical URL of the thing plus a fragment, for example `/it/shop/<vendor>/#organization`)
and reference each other by `@id`, so one entity is described once per site and linked
everywhere:

- `Organization` for the platform operator (from `current_operator()`), with logo, legal name,
  VAT ID and contact point; `WebSite` with a `SearchAction`; every page's `WebPage` (or the
  subtype `CollectionPage`, `ProfilePage`, `ItemPage`, `AboutPage`), its `BreadcrumbList` and
  primary image.
- Vendors as `Organization` nodes on their storefront, used as `seller` in every `Offer`;
  shared `MerchantReturnPolicy` and `OfferShippingDetails` nodes per vendor.
- `Product` with `Offer` or `AggregateOffer` (variants), `brand`, `gtin`/`mpn`, ratings from
  verified reviews only; listings and taxonomy pages as `CollectionPage` with an `ItemList`.
- Blog: `BlogPosting` with `author` pointing to a `Person` node (`ProfilePage` on the author
  page, CMS.3), `publisher` pointing to the operator `Organization`; events as `Event` (spec 15).
- Language: `inLanguage` on every node, the text in the page language only, never a fallback in
  another language; no node for content that is not published in that language.

JSON-LD is the only format (no microdata or RDFa, which Google and other consumers do not need
in addition). GraphQL is not used for SEO: search engines and AI crawlers read JSON-LD, sitemaps,
feeds and `llms.txt`; machine clients use the REST API and the MCP server. A GraphQL API would
add a second API surface (query cost limits, its own permission layer) for no current consumer.

### Feeds and Merchant

```python
def merchant_items(lang, country) -> Iterable[MerchantItem]
    # id = legacy_wp_id if present (continuity of Merchant history), otherwise public_id
    # VAT-inclusive price computed with the tax engine for the feed's country (not a fixed 22%)
    # shipping per country and band from the shipping tables (not hardcoded)
    # Google category: product → category → vendor → default
    # g:gtin and g:mpn + g:brand from the product fields (Q-33), never the vendor SKU;
    # identifier_exists = no when the product has neither
    # on_request and catalog_only products are left out: their stored price is never public (Q-31)
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
| CMS.1 | Wagtail: site, locales, base page types, per-language navigation and footer | P1 | 8 | ARC.3 | Home and static pages in every configured language |
| CMS.2 | Reusable StreamField blocks (hero, CTA, FAQ, choosers) | P1 | 8 | CMS.1 | An editor builds the home page without code |
| CMS.10 | Product grid block (StreamField): products by category, term, brand or vendor, using the CAT.8 listing query | P1 | 3 | CMS.2, CAT.8 | An editor places a product grid filtered by a term on a page and it renders the CAT.8 results |
| CMS.3 | Blog with scheduling, author, product/term/vendor chooser | P1 | 8 | CMS.2 | Migrated article published |
| CMS.4 | TermLandingPage and VendorLandingPage | P1 | 10 | CMS.2, CAT.8 | A term page with its products |
| CMS.5 | Versioned LegalPage + VendorRecruitmentPage (plans, FAQ) leading to the VEN.22 application form | P1 | 6 | CMS.1, VEN.5 | Legal page versions kept per language, so a translated legal page has its own version and effective date (Q-42); the recruitment page shows the plans and links to the application |
| CMS.8 | Image source, licence and credit: custom Wagtail image model `MediaImage` in `core` with licence, source, author, credit and permission fields, staff-editable `ImageLicence` allow list seeded per §10.2, upload validation in admin and services, `image_credit` component and per-page image credits disclosure, review list for migrated images without metadata; works with local storage, ARC.15 later moves only the storage backend | P1 | 5 | CMS.1 | An upload without licence or author is refused; an NC licence cannot be added to the allow list; a CC BY image shows its credit in the caption, a CC BY hero in the page credits disclosure; a CC0 image shows none; `WAGTAILIMAGES_IMAGE_MODEL` points to `MediaImage` before CAT.4 |
| CMS.9 | Launch countdown StreamField block: fields per §10.2, `countdown_state` pure function, server-rendered absolute date and expired state, page cache capped at the target, CSP-safe ES module with visitor time zone, minute updates and pause button, single polite announcement at expiry | P1 | 4 | CMS.2, THM.4 | Without JavaScript the block shows the absolute date and the CTA; with it the remaining time updates once a minute in the visitor's time zone and the pause button stops it; after the target the server and the module both show the expired text and link; no `aria-live` on the counter; no CSP violation and no axe violation on a page with the block |
| CMS.6 | Reviews: model, purchase verification, supplier import, moderation, vendor reply | P2 | 10 | ORD.2 | AggregateRating schema only with valid reviews |
| SEO.1 | JSON-LD Product/Offer/Breadcrumb/ReturnPolicy/ShippingDetails, robots, canonical; GTIN and MPN from the product fields, no price for on-request and catalogue-only products (Q-31, Q-33) | P1 | 10 | CAT.7, SHP.3 | Rich Results Test with no errors; an on-request product's JSON-LD has no price |
| SEO.2 | Per-language and per-type sitemaps, generated `llms.txt` | P1 | 4 | I18N.6 | Valid sitemaps |
| SEO.3 | Google feed (EN for every product, IT for products with a published IT translation, D-18) with legacy IDs, prices from the tax engine, shipping from tables; GTIN, brand and MPN from the product fields; on-request and catalogue-only products left out (Q-31, Q-33) | P1 | 8 | TAX.3, SHP.11 | Feed validated in STAGE Merchant Center; no item carries the vendor SKU as MPN; no on-request product is in the feed |
| SEO.4 | Merchant API sync + Meta feed | P2 | 8 | SEO.3 | Daily delta report |
| SEO.5 | Cookie consent + Consent Mode v2 + e-commerce events | P1 | 8 | ORD.5 | No tags fire before consent |
| SEO.6 | Anonymous page cache, responsive renditions, CWV budget | P2 | 6 | THM.3 | LCP < 2.5 s on product page |
| SEO.7 | Site-wide schema.org `@graph` (owner question 2026-11-09): one JSON-LD block per page from `seo.page_graph`, stable `@id` nodes for the operator `Organization`, `WebSite` + `SearchAction`, `WebPage` subtypes, `BreadcrumbList`, vendor `Organization` as `seller`, shared return and shipping nodes, `Product`/`Offer`/`AggregateOffer`, `CollectionPage` + `ItemList`, `BlogPosting` + `Person`/`ProfilePage`, `Event`; `inLanguage` and no cross-language fallback; SEO.1 and EVT.10 return nodes into it | P1 | 8 | SEO.1, CMS.3 | Each page type has a snapshot test of its graph; every `@id` reference resolves to a node in the same graph or a canonical URL; a schema check (types, required and recommended properties of the Google rich results for Product, Breadcrumb, Article, Event, ProfilePage) runs in CI; a page has exactly one JSON-LD script |
| AI.1 | MCP server: auth, read tools (catalog, orders, quotes, simulations, reports) | P2 | 12 | CORE.7 | Tools usable from Claude Code with scopes |
| AI.2 | Mutating MCP tools with dry-run, confirmation and audit | P2 | 10 | AI.1 | Every mutation has a diff and an audit entry |
| AI.3 | Read-only public catalog API for agents | P3 | 6 | CAT.9 | Search by taxonomy term via API |
| AI.4 | Evaluation of agentic commerce protocols (ACP, WebMCP) and prototype | P3 | 12 | PAY.1, AI.3 | ADR with recommendation |
