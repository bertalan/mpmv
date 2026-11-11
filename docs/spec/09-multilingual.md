# 09 — Multilingual

## 9.1 How it works today

- WPML + WooCommerce Multilingual + String Translation + WPML SEO. The primary language today is EN, IT is translated.
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
| Events (`EventPage`) | **Wagtail i18n** (`Locale`, `TranslatableMixin`, per-locale trees), any enabled language as the original, optional translations ([15](15-events.md)) | Exception to D-18 for vendor-authored events (owner decision Q-72) |
| Interface strings, emails, messages | **gettext** (`.po`/`.mo`) | Django standard; the `i18n/add_it_translations.py` scripts already exist |
| Tax wording (art. 74, reverse charge, export) | gettext keys referenced by `legal_note_key` | The text follows the document language; it is not a free-text field to be duplicated |
| Vendor legal documents (vendor contract, other vendor-facing terms) | Versioned documents with one text per language (`VendorContractTranslation`, vendor clauses and custom uploads, see [04](04-vendors-commissions-ledger.md)) | Not visitor content: the vendor accepts the exact text in its own language, with a prevailing language per version |

Rules:

- **Language order** (D-18, updated 2026-11-07): five languages at go-live, in this order: EN (primary
  and source), IT, DE, FR, ES. The order is configuration (`settings.LANGUAGES`): adding a language
  means adding one entry plus its translations, never code changes.
- Language-prefix routing (`/` = EN, `/it/`, `/de/`, `/fr/`, `/es/`, `/<code>/` for later languages),
  language switcher and translation tables ready for all five languages; automatic `hreflang` and
  canonical.
- Interface text versus visitor content (D-18): every fixed interface string is translated into
  all five languages at go-live: buttons, cart, checkout, account and MFA pages, error and validation
  messages, emails, notifications, and the legal and tax wording keys used on documents
  (`legal_note_key`). The process is an AI draft followed by human review of checkout, emails and
  legal or tax wording (I18N.7). Content shown to visitors (products, categories and taxonomy terms,
  vendor storefront texts, CMS pages, blog, landing pages) follows two rules at go-live (owner
  decision of 2026-11-07). The **existing Italian texts** of the current site are migrated and
  published in IT (MIG.3 for vendors, catalog and taxonomies, MIG.7 for pages and blog), so the
  current IT URLs keep their content. **New** visitor content created after the migration (new
  products from the pipelines, new pages and articles) stays EN only until after go-live, and DE, FR
  and ES visitor content comes after go-live; the owner then decides whether and how to translate
  it (I18N.8). **Customer legal and checkout pages are the exception** (owner, 2026-11-07, Q-42):
  terms of sale, privacy, withdrawal, shipping and returns are translated into all five languages
  at go-live (AI draft, native-speaker review, legal counsel check per language) and never fall
  back to EN (I18N.9). Vendor contracts follow the vendor's language (VEN.13, VEN.14).
- **Explicit fallback**: except for events (below), if the published translation is missing, the primary language (the first of `MPMV_LANGUAGES`, EN by default; I18N.15) is shown with a notice (the default and LIVE primary language is English), and the
  page is not included in that language's sitemap (never empty or duplicate content indexed).
  A fallback copy declares the EN page as its canonical URL (no `noindex`, so the two signals never
  conflict), and `hreflang` lists only the languages that have a published translation. At go-live
  this applies to all visitor content in DE, FR and ES, and to new visitor content in IT: a
  migrated page with its IT translation lists EN and IT in `hreflang`, a new page lists EN only.
- Images (D-18): an image is language neutral by default. An image can optionally be dedicated to
  one language (for example packaging printed in German). Replacement rule: when a product, or a CMS
  gallery, has at least one image dedicated to the requested language, that language shows only its
  dedicated images; otherwise it shows the neutral images. Alt text and captions are always
  translatable text (a `TranslationModel` with EN fallback), because they matter for SEO and
  accessibility. On CMS pages, wagtail-localize lets a translator override an image for one locale
  (spec 10, I18N.3).
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
- Vendor legal documents are an exception to the rule that new content is English only until after
  go-live (owner decision, 2026-11-07). They exist in every vendor language from the start (IT,
  EN, DE, FR, ES), are never machine drafts, and are not part of the CMS translation workflow or
  the visitor fallback: when the vendor's language has no text, the vendor accepts the English
  text with an explicit acknowledgement (VEN.14). The legal translation of these texts is the
  owner's business task, not a backlog task.
- **Events are an exception to D-18** (owner decision Q-72, 2026-11-07; spec 15, EVT.8). Vendors
  (and staff) write an event in any enabled language (EN, IT, DE, FR, ES, and later languages),
  with no required source language: English is not required. Translations into the other enabled
  languages are optional and use Wagtail's i18n, the mechanism of CMS pages (`Locale`,
  `TranslatableMixin`, pages linked by `translation_key`). An event has a URL only in the
  languages it has a published translation in; there are no fallback copies, `hreflang` lists
  only the existing translations and each language's sitemap only that language's translations.
  The listings and the RSS and ICS feeds of a language show each event in that language's
  translation, or else in the event's **original language**, marked with a `lang` attribute (and
  `dc:language` in RSS, `LANGUAGE` in ICS) and linking to the original page. The EN fallback with
  a notice does not apply to events.

## 9.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| I18N.1 | Language configuration, URL prefixes, middleware, language switcher | P1 | 4 | ARC.3, ARC.13 | `/it/`, `/de/`, `/fr/` and `/es/` active on STAGE |
| I18N.2 | Abstract base `TranslationModel` with status, `source_hash`, fallback | P1 | 6 | ARC.6 | Reused by catalog and vendor |
| I18N.3 | wagtail-localize for CMS pages: image fields and image blocks overridable per locale, alt text and captions as translatable segments, CMS gallery replacement rule (D-18) | P1 | 6 | CMS.1 | Translated page with sync; a translator replaces one image in one locale only |
| I18N.4 | Translation queue in admin (by language, vendor, status) + AI draft translation | P1 | 8 | I18N.2, ING.5 | EN edit → every other language goes back to "needs review" |
| I18N.5 | gettext catalogs for templates and emails; tax wording as keys | P1 | 6 | ORD.9 | No hardcoded strings (lint) |
| I18N.6 | hreflang, canonical, per-language sitemap with fallback excluded | P1 | 4 | SEO.1 | Validation on a sample of URLs: fallback copies have the EN canonical and are absent from hreflang and from the language sitemap |
| I18N.7 | Interface strings in IT, DE, FR and ES for go-live (D-18): gettext catalogs for templates, emails, notifications, error and validation messages, MFA pages and tax/legal wording keys, including the language-switch notice \"This content is not available in this language.\" (I18N.11); AI draft, human review of checkout, emails and legal/tax wording; coverage report | P1 | 10 | I18N.5 | Coverage report: 100% of interface strings translated in IT, DE, FR and ES; checkout, email and legal/tax wording reviewed by a human |
| I18N.8 | Visitor content translation after go-live (D-18): products, categories, taxonomy terms, vendor storefronts, CMS pages, blog and landing pages, with the method the owner chooses (AI, professional or other) through the I18N.4 queue; per-language coverage report | P2 | 6 | I18N.4, I18N.3, I18N.6 | Per-language coverage report; only published translations appear in hreflang and sitemaps |
| I18N.9 | Customer legal and checkout pages at go-live (Q-42): terms of sale, privacy, withdrawal, shipping and returns as `LegalPage` translations in EN, IT, DE, FR and ES through wagtail-localize, each language with its own version and effective date; AI draft, native-speaker review and legal counsel check recorded per language; no EN fallback for these pages | P1 | 6 | CMS.5, I18N.3 | Every one of the five pages is published in all five languages; a go-live readiness check fails when a legal or checkout page lacks a published translation in a go-live language; a translation of a legal page shows its own effective date |
| I18N.10 | User text stored in NFC (SEC.5 hostile-input finding F3, proposed on PR #166, registered by coherence check 7; owner: "non-NFC forms (store NFC)"): forms, API schemas and importers normalise free text to Unicode NFC at the service boundary; the SKU match key of spec 03 stays NFKC | P2 | 3 | — | Text entered through a form, the API or an importer is stored in NFC; the F3 strict xfail in `tests/integration/test_hostile_input_outputs.py` passes and its mark is removed |
| I18N.11 | Language switch stays on the same object (owner bug, priority 1): the public switch leads to the same page, listing, storefront or event in the chosen language (a product page when it exists), from the one `{language: URL}` mapping that also builds the `hreflang` alternates (`apps.core.i18n`); a language with no translation of the object leads to that language's home and shows a notice that the content is not available in it | P1 | 3 | I18N.1, I18N.6 | A term, brand, vendor and CMS page switch to their translation (the `hreflang` set equals the switch targets without notice); an untranslated object leads to the target home with the notice once; e2e screenshot of the switch |
| I18N.12 | The stored language applies on unprefixed areas (owner bug on STAGE v0.1.4, priority 1, follow-up of I18N.11): the language switcher sets no cookie, so a middleware right after `AuthenticationMiddleware` activates a signed-in user's `preferred_language` for requests without a language prefix (Manager, account area, MFA pages, admins) and sets `request.LANGUAGE_CODE`; prefixed public URLs keep the prefix as the only source; anonymous users, an unknown or no longer configured language change nothing | P1 | 1 | I18N.1, I18N.11 | A signed-in user who switches language in the Manager sees the Manager in that language and can switch back; a prefixed public URL ignores `preferred_language`; anonymous and unknown-language cases unchanged; e2e screenshot of the Manager switch |
| I18N.15 | Operator-chosen site languages, English not mandatory (owner request 2026-11-11): the setting `MPMV_LANGUAGES` is an ordered list of language codes; the first is the primary (source) language and has no URL prefix; the list feeds `LANGUAGES`, `LANGUAGE_CODE` and `WAGTAIL_CONTENT_LANGUAGES`; the default stays `en,it,de,fr,es` (D-18). MPMV LIVE keeps English as the primary and source language with Italian as the base translation, DE, FR and ES being further translations as today (owner, 2026-11-11); the setting exists for other installations, which define their own primary language and translations freely, and the non-English CI job proves the product side. Adding a language later is a setting; removing one or changing the primary language is a management command with redirects (`dry_run`, `AuditLog`), never a silent settings change. Migration note: the legacy English content maps to a chosen enabled language. Code already reads `settings.LANGUAGE_CODE` almost everywhere; only `settings/base.py` hardcodes EN | P1 | 8 | I18N.1, I18N.9, I18N.7 | (1) A system check refuses an unknown language code and warns or refuses a language whose interface catalog is incomplete (msgids are English, so a gap would leak English); (2) the go-live check requires the legal and checkout pages in every enabled language (I18N.9); (3) adding a language is a setting, while removing one or changing the primary language is a management command that creates redirects, and a settings change alone is refused by a check; (4) CI runs one job with a non-English configuration (for example `it,de`) and no test assumes `en`; (5) spec 09 says "the primary language", not "EN is the base"; the legacy English content maps to a chosen enabled language in the migration |
