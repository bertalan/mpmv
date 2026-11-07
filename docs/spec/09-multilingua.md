# 09 — Multilingua

## 9.1 Come funziona oggi

- WPML + WooCommerce Multilingual + String Translation + WPML SEO. EN lingua base, IT tradotta.
- **Ogni prodotto, categoria e tag esiste due volte** (post o termine EN e IT legati da `trid`).
  Stock e prezzi vanno tenuti allineati; le tassonomie IT vanno riapplicate a mano.
- Tradotti anche: attributi (String Translation), piani membership, stringhe WCFM (contesto `WCfM`),
  descrizioni e policy dei negozi (meta per lingua), opzioni marketplace (`_en` / `_it`).
- Problemi documentati: molti prodotti IT su categorie EN; sidebar negozi IT vuota; slug `-2` e
  prodotti triplicati da import errato; WPML intercetta `wp_set_object_terms` (servono SQL diretti);
  `wpml_add_string_translation` fallisce in silenzio; campi mono-lingua forzati in bilingue;
  aggiornamenti premium bloccati su STAGE.

## 9.2 Come funzionerà

| Contenuto | Soluzione | Perché |
|---|---|---|
| Prodotti, categorie, termini di tassonomia, brand, attributi, vendor | **Tabelle di traduzione esplicite** (`*Translation`: lingua + campi testuali) | Una sola entità: SKU, prezzo, stock, peso e relazioni esistono una volta. Aggiungere il tedesco = nuove righe, nessuna migrazione di schema |
| Pagine CMS, blog, landing, testi legali | **wagtail-localize** (alberi per locale, sincronizzazione, stato traduzione) | Strumento nativo Wagtail |
| Stringhe di interfaccia, email, messaggi | **gettext** (`.po`/`.mo`) | Standard Django; gli script `i18n/add_it_translations.py` esistono già |
| Diciture fiscali (art. 74, reverse charge, export) | Chiavi gettext referenziate da `legal_note_key` | Il testo segue la lingua del documento, non è un campo libero da duplicare |

Regole:

- Routing con prefisso di lingua (`/` = EN, `/it/`, `/de/`); `hreflang` e canonical automatici.
- **Fallback esplicito**: se manca la traduzione pubblicata si mostra la lingua base con avviso, e la
  pagina non entra nella sitemap di quella lingua (mai contenuto vuoto né duplicato indicizzato).
- **Workflow di traduzione**: ogni `*Translation` ha stato `machine` → `reviewed` → `published` e un
  `source_hash`; se cambia il testo EN, le altre lingue tornano "da rivedere". Coda in admin per
  lingua e vendor.
- Traduzione automatica (AI) come bozza, con revisione umana dove richiesto; regola redazionale
  invariata: titoli in sentence case.
- Email e documenti nella lingua del destinatario (`Order.language`, `User.preferred_language`).

## 9.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| I18N.1 | Configurazione lingue, prefissi URL, middleware, selettore lingua | P1 | 4 | ARC.3 | `/it/` e `/de/` attivi su STAGE |
| I18N.2 | Base astratta `TranslationModel` con stato, `source_hash`, fallback | P1 | 6 | ARC.6 | Riusata da catalogo e vendor |
| I18N.3 | wagtail-localize per le pagine CMS | P1 | 4 | CMS.1 | Pagina tradotta con sync |
| I18N.4 | Coda traduzioni in admin (per lingua, vendor, stato) + traduzione AI in bozza | P2 | 8 | I18N.2, ING.5 | Modifica EN → IT torna "da rivedere" |
| I18N.5 | Cataloghi gettext per template ed email; diciture fiscali come chiavi | P1 | 6 | ORD.9 | Nessuna stringa hardcoded (lint) |
| I18N.6 | hreflang, canonical, sitemap per lingua con fallback escluso | P1 | 4 | SEO.1 | Validazione su campione di URL |
