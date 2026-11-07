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
| `EventIndexPage` / `EventPage` | Events of vendors and of the platform: closures, promotions, trade fairs and meetings, with RSS and ICS feeds and maps ([15](15-events.md), `events` app) | the block library above, the launch countdown, structured dates, recurrence, location and coordinates |

Events have their own page type, `EventPage`, in the `events` app ([15](15-events.md)). This
supersedes owner decision Q-61 for events, which had made them blog articles. There are still no
page types for tours, trails or packages: they are product categories (CAT.2) or article content.
The launch countdown block below is also available in `EventPage` bodies.

Taxonomy and vendor pages combine editorial content with catalog data: they are the SEO strong point
("browse by taxonomy term") and today they are fragmented across tags, categories and articles.

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
images (CAT.4), vendor logos and banners share it:

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
    # + isRelatedTo (and sector-specific properties, if configured) + shippingDetails + hasMerchantReturnPolicy
    # + aggregateRating (verified reviews only)
def breadcrumb_jsonld(obj, lang) -> dict
def robots_meta(request, obj) -> str            # noindex on 404, empty search, pagination beyond a threshold
def sitemaps() -> dict[str, Sitemap]           # per language and type; excludes fallbacks
def llms_txt(lang) -> str                       # generated from vendors, brands and taxonomy terms, key pages, event feeds
def event_jsonld(page, lang) -> dict | None     # schema.org Event with Place, PostalAddress, GeoCoordinates (spec 15, EVT.10)
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
| CMS.1 | Wagtail: site, locales, base page types, per-language navigation and footer | P1 | 8 | ARC.3 | Home and static pages in every configured language |
| CMS.2 | Reusable StreamField blocks (hero, product grid, CTA, FAQ, choosers) | P1 | 10 | CMS.1, CAT.8 | An editor builds the home page without code |
| CMS.3 | Blog with scheduling, author, product/term/vendor chooser | P1 | 8 | CMS.2 | Migrated article published |
| CMS.4 | TermLandingPage and VendorLandingPage | P1 | 10 | CMS.2, CAT.8 | A term page with its products |
| CMS.5 | Versioned LegalPage + VendorRecruitmentPage with application form | P1 | 6 | CMS.1, VEN.5 | Application arrives in admin |
| CMS.8 | Image source, licence and credit: custom Wagtail image model `MediaImage` in `core` with licence, source, author, credit and permission fields, staff-editable `ImageLicence` allow list seeded per §10.2, upload validation in admin and services, `image_credit` component and per-page image credits disclosure, review list for migrated images without metadata | P1 | 5 | CMS.1, ARC.15 | An upload without licence or author is refused; an NC licence cannot be added to the allow list; a CC BY image shows its credit in the caption, a CC BY hero in the page credits disclosure; a CC0 image shows none; `WAGTAILIMAGES_IMAGE_MODEL` points to `MediaImage` before CAT.4 |
| CMS.9 | Launch countdown StreamField block: fields per §10.2, `countdown_state` pure function, server-rendered absolute date and expired state, page cache capped at the target, CSP-safe ES module with visitor time zone, minute updates and pause button, single polite announcement at expiry | P1 | 4 | CMS.2, THM.4 | Without JavaScript the block shows the absolute date and the CTA; with it the remaining time updates once a minute in the visitor's time zone and the pause button stops it; after the target the server and the module both show the expired text and link; no `aria-live` on the counter; no CSP violation and no axe violation on a page with the block |
| CMS.6 | Reviews: model, purchase verification, supplier import, moderation, vendor reply | P2 | 10 | ORD.2 | AggregateRating schema only with valid reviews |
| SEO.1 | JSON-LD Product/Offer/Breadcrumb/ReturnPolicy/ShippingDetails, robots, canonical | P1 | 10 | CAT.7, SHP.3 | Rich Results Test with no errors |
| SEO.2 | Per-language and per-type sitemaps, generated `llms.txt` | P1 | 4 | I18N.6 | Valid sitemaps |
| SEO.3 | Google feed (EN for every product, IT for products with a published IT translation, D-18) with legacy IDs, prices from the tax engine, shipping from tables | P1 | 8 | TAX.3, SHP.11 | Feed validated in STAGE Merchant Center |
| SEO.4 | Merchant API sync + Meta feed | P2 | 8 | SEO.3 | Daily delta report |
| SEO.5 | Cookie consent + Consent Mode v2 + e-commerce events | P1 | 8 | ORD.5 | No tags fire before consent |
| SEO.6 | Anonymous page cache, responsive renditions, CWV budget | P2 | 6 | THM.3 | LCP < 2.5 s on product page |
| AI.1 | MCP server: auth, read tools (catalog, orders, quotes, simulations, reports) | P2 | 12 | CORE.7 | Tools usable from Claude Code with scopes |
| AI.2 | Mutating MCP tools with dry-run, confirmation and audit | P2 | 10 | AI.1 | Every mutation has a diff and an audit entry |
| AI.3 | Read-only public catalog API for agents | P3 | 6 | CAT.9 | Search by taxonomy term via API |
| AI.4 | Evaluation of agentic commerce protocols (ACP, WebMCP) and prototype | P3 | 12 | PAY.1, AI.3 | ADR with recommendation |
