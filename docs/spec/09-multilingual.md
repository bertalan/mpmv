# 09 — Multilingual

## 9.1 How it works today

- WPML + WooCommerce Multilingual + String Translation + WPML SEO. EN is the base language, IT is translated.
- **Every product, category and tag exists twice** (EN and IT post or term linked by `trid`).
  Stock and prices must be kept in sync; IT taxonomies have to be reapplied by hand.
- Also translated: attributes (String Translation), membership plans, WCFM strings (`WCfM` context),
  vendor storefront descriptions and policies (per-language meta), marketplace options (`_en` / `_it`).
- Documented problems: many IT products on EN categories; empty IT vendor storefront sidebar; `-2` slugs and
  triplicated products from a faulty import; WPML intercepts `wp_set_object_terms` (direct SQL is needed);
  `wpml_add_string_translation` fails silently; single-language fields forced into bilingual;
  premium updates blocked on STAGE.

## 9.2 How it will work

| Content | Solution | Why |
|---|---|---|
| Products, categories, taxonomy terms, brands, attributes, vendors | **Explicit translation tables** (`*Translation`: language + text fields) | A single entity: SKU, price, stock, weight and relations exist once. Adding German = new rows, no schema migration |
| CMS pages, blog, landing pages, legal texts | **wagtail-localize** (per-locale trees, synchronization, translation status) | Native Wagtail tool |
| Interface strings, emails, messages | **gettext** (`.po`/`.mo`) | Django standard; the `i18n/add_it_translations.py` scripts already exist |
| Tax wording (art. 74, reverse charge, export) | gettext keys referenced by `legal_note_key` | The text follows the document language; it is not a free-text field to be duplicated |

Rules:

- Language-prefix routing (`/` = EN, `/it/`, `/de/`); automatic `hreflang` and canonical.
- **Explicit fallback**: if the published translation is missing, the base language is shown with a notice, and the
  page is not included in that language's sitemap (never empty or duplicate content indexed).
- **Translation workflow**: every `*Translation` has a status `machine` → `reviewed` → `published` and a
  `source_hash`; if the EN text changes, the other languages go back to "needs review". Admin queue per
  language and vendor.
- Machine (AI) translation as a draft, with human review where required; editorial rule
  unchanged: titles in sentence case.
- Emails and documents in the recipient's language (`Order.language`, `User.preferred_language`).

## 9.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| I18N.1 | Language configuration, URL prefixes, middleware, language switcher | P1 | 4 | ARC.3 | `/it/` and `/de/` active on STAGE |
| I18N.2 | Abstract base `TranslationModel` with status, `source_hash`, fallback | P1 | 6 | ARC.6 | Reused by catalog and vendor |
| I18N.3 | wagtail-localize for CMS pages | P1 | 4 | CMS.1 | Translated page with sync |
| I18N.4 | Translation queue in admin (by language, vendor, status) + AI draft translation | P1 | 8 | I18N.2, ING.5 | EN edit → IT and DE go back to "needs review" |
| I18N.5 | gettext catalogs for templates and emails; tax wording as keys | P1 | 6 | ORD.9 | No hardcoded strings (lint) |
| I18N.6 | hreflang, canonical, per-language sitemap with fallback excluded | P1 | 4 | SEO.1 | Validation on a sample of URLs |
| I18N.7 | German content for go-live (D-18): AI draft DE for catalog, taxonomy terms, vendor storefronts and CMS pages; human review of legal pages, checkout and emails; coverage report | P1 | 8 | I18N.4, I18N.3, MIG.7 | DE coverage report: 100% of legal/checkout/email texts reviewed, catalog drafts complete |
