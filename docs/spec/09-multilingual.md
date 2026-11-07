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
| Vendor legal documents (vendor contract, other vendor-facing terms) | Versioned documents with one text per language (`VendorContractTranslation`, vendor clauses and custom uploads, see [04](04-vendors-commissions-ledger.md)) | Not visitor content: the vendor accepts the exact text in its own language, with a prevailing language per version |

Rules:

- **Language order** (owner decision, 2026-11-07): EN is the primary and source language, then IT, then
  DE, then any later language. The order is configuration (`settings.LANGUAGES`): adding a language
  means adding one entry plus its translations, never code changes.
- Language-prefix routing (`/` = EN, `/it/`, `/de/`, `/<code>/` for later languages); automatic
  `hreflang` and canonical.
- **Explicit fallback**: if the published translation is missing, the base language (EN) is shown with a notice, and the
  page is not included in that language's sitemap (never empty or duplicate content indexed).
- **Translation workflow**: every `*Translation` has a status `machine` → `reviewed` → `published` and a
  `source_hash`; if the EN text changes, the other languages go back to "needs review". Admin queue per
  language and vendor.
- **Shared base** (I18N.2): every `*Translation` table subclasses `apps.core.models.TranslationModel`
  (language, status, `source_hash`; "needs review" is the `machine` status) and declares
  `translation_of` (the FK to the translated entity) and `translated_fields`, with one row per entity
  and language. Texts for visitors are read through `resolve_translation`, which returns the published
  row or the EN row with a fallback flag. A system check enforces the contract, so catalog tables
  (CAT tasks) reuse it as they are added. Language fields use `LanguageField`, whose choices are read
  from settings at runtime, so a new language needs no migration.
- Machine (AI) translation as a draft, with human review where required; editorial rule
  unchanged: titles in sentence case.
- Emails and documents in the recipient's language (`Order.language`, `User.preferred_language`).
- Vendor legal documents are an exception to the rule that content is English only until after
  go-live (owner decision, 2026-11-07). They exist in every vendor language from the start (IT,
  EN, DE, FR, ES), are never machine drafts, and are not part of the CMS translation workflow or
  the visitor fallback: when the vendor's language has no text, the vendor accepts the English
  text with an explicit acknowledgement (VEN.14). The legal translation of these texts is the
  owner's business task, not a backlog task.

## 9.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| I18N.1 | Language configuration, URL prefixes, middleware, language switcher | P1 | 4 | ARC.3, ARC.13 | `/it/` and `/de/` active on STAGE |
| I18N.2 | Abstract base `TranslationModel` with status, `source_hash`, fallback | P1 | 6 | ARC.6 | Reused by catalog and vendor |
| I18N.3 | wagtail-localize for CMS pages | P1 | 4 | CMS.1 | Translated page with sync |
| I18N.4 | Translation queue in admin (by language, vendor, status) + AI draft translation | P1 | 8 | I18N.2, ING.5 | EN edit → IT and DE go back to "needs review" |
| I18N.5 | gettext catalogs for templates and emails; tax wording as keys | P1 | 6 | ORD.9 | No hardcoded strings (lint) |
| I18N.6 | hreflang, canonical, per-language sitemap with fallback excluded | P1 | 4 | SEO.1 | Validation on a sample of URLs |
| I18N.7 | German content for go-live (D-18): AI draft DE for catalog, taxonomy terms, vendor storefronts and CMS pages; human review of legal pages, checkout and emails; coverage report | P1 | 8 | I18N.4, I18N.3, MIG.7 | DE coverage report: 100% of legal/checkout/email texts reviewed, catalog drafts complete |
