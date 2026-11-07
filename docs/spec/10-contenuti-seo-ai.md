# 10 — Contenuti (Wagtail), SEO, feed e canali AI

## 10.1 Come funziona oggi

- Pagine e blog in WordPress con Elementor (tema `storefront` + child theme custom); pagine
  "Become a Vendor", "Vendor Membership", affiliazione; blog bilingue con script `scripts/blog/`.
- **WPSSO Core Premium** + add-on: JSON-LD Product/Offer/Breadcrumb/MerchantReturnPolicy/
  ShippingDetails/AggregateRating, Open Graph, sitemap, feed Google Merchant e Meta.
- mu-plugin SEO: relazioni schema.org tra prodotti e termini (`isRelatedTo` e simili) nel JSON-LD; robots
  `max-snippet:-1`; noindex sui 404; ricerca interna indicizzabile solo con risultati; redirect autori;
  correzione breadcrumb IT.
- `google-feed.php`: feed RSS EN/IT con cache 6 h, IVA **22% fissa**, spedizioni hardcoded,
  `g:id` = **ID post WordPress**, categoria Google da `_wpsso_meta` o per vendor (override per vendor, default generico),
  GTIN o MPN = SKU.
- `scripts/google_merchant_api/`: sync via Merchant API (data source EN+IT, report dei delta).
- `llms.txt` statico (48 righe). Analytics con Site Kit e Google for WooCommerce; cookie con Complianz.
- Roadmap MCP interna: MCP server-side per admin e vendor, poi WebMCP lato cliente.

## 10.2 Come funzionerà

### Pagine Wagtail

| Page type | Uso | Blocchi StreamField |
|---|---|---|
| `HomePage` | Home | hero, griglia prodotti (query), termini in evidenza, vendor in evidenza, CTA |
| `StandardPage` | Chi siamo, contatti, FAQ | rich text, immagini, FAQ (con schema FAQPage), CTA |
| `LegalPage` | Termini, privacy, cookie, recesso, resi | testo versionato con data di efficacia |
| `BlogIndexPage` / `BlogPostPage` | Articoli bilingui, vendor in evidenza | testo, gallerie, **chooser prodotti/termini/vendor** (niente URL a mano), autore, schedulazione |
| `TermLandingPage` | Pagina editoriale per un termine di tassonomia | intro SEO, prodotti del termine per categoria, articoli correlati |
| `VendorLandingPage` | Racconto del vendor (heritage, laboratorio) oltre la vetrina standard | storia, immagini, prodotti in evidenza |
| `VendorRecruitmentPage` | "Diventa venditore" con piani e form di candidatura | piani (da `VendorPlan`), FAQ, form |

Le pagine di tassonomia e vendor uniscono contenuto editoriale e dati del catalogo: sono il punto di forza
SEO ("navigazione per termine di tassonomia") e oggi sono frammentate tra tag, categorie e articoli.

### SEO tecnica (app `seo`)

```python
def product_jsonld(product, lang) -> dict
    # Product + Offer (prezzo IVA incl. per il paese di default) + brand + gtin/mpn
    # + isRelatedTo (e proprietà di settore, se configurate) + shippingDetails + hasMerchantReturnPolicy
    # + aggregateRating (solo recensioni verificate)
def breadcrumb_jsonld(obj, lang) -> dict
def robots_meta(request, obj) -> str            # noindex su 404, ricerca vuota, paginazioni oltre soglia
def sitemaps() -> dict[str, Sitemap]           # per lingua e tipo; esclude i fallback
def llms_txt(lang) -> str                       # generato da vendor, marche e termini di tassonomia, pagine chiave
```

### Feed e Merchant

```python
def merchant_items(lang, country) -> Iterable[MerchantItem]
    # id = legacy_wp_id se presente (continuità dello storico Merchant), altrimenti public_id
    # prezzo IVA inclusa calcolato col motore fiscale per il paese del feed (non 22% fisso)
    # spedizione per paese e fascia dalle tabelle shipping (non hardcoded)
    # categoria Google: prodotto → categoria → vendor → default
def sync_merchant(lang) -> SyncReport            # Merchant API, delta, nessuna cancellazione massiva
def meta_catalog_feed(lang) -> Response          # feed Meta Commerce
```

### Analytics e consenso

- Banner di consenso con Consent Mode v2 (CMP certificata o soluzione self-hosted, D-10).
- Eventi e-commerce standard (view_item, add_to_cart, begin_checkout, purchase) anche server-side
  dopo consenso; dashboard interna minima (conversione, vendite per vendor).

### Recensioni

- Recensioni prodotto da **acquisto verificato** + recensioni importate dai fornitori (con origine
  dichiarata e data originale, come oggi). Moderazione staff, risposta del vendor.
- Trasparenza sulle recensioni (direttiva Omnibus): spiegare come sono verificate.

### Canali AI

1. **Server MCP per la gestione** (P2): adattatore sottile sui servizi, autenticato come `ApiClient`
   con scope.
   - Tool di lettura: cerca prodotti, stato traduzioni, simula IVA o spedizione, report import,
     ordini in ritardo, preventivi aperti.
   - Tool mutativi solo con dry-run e diff; esecuzione dopo conferma umana; audit di ogni chiamata.
   - Sostituisce gli script `diagnose_*` / `fix_*` e il flusso attuale "AI + route API + script ad hoc".
   - I testi di prodotto e feed sono **input non fidato** (prompt injection): mai eseguire istruzioni
     contenute nei dati.
2. **Catalogo leggibile dagli agenti** (P2): `llms.txt` generato, JSON-LD completo, feed aggiornati,
   API pubblica di sola lettura del catalogo (ricerca per termine di tassonomia).
3. **Commercio via agenti** (P3): protocolli come l'Agentic Commerce Protocol (Stripe e OpenAI) o
   WebMCP lato browser. Da valutare quando i protocolli si stabilizzano. L'architettura a servizi e
   l'uso di Stripe li rendono un adattatore in più, non una riscrittura.

### Performance

Cache pagine anonime (invalidazione alla pubblicazione), immagini responsive (WebP/AVIF, `srcset`),
CSS e JS minimi, budget Core Web Vitals definito prima del frontend (LCP < 2,5 s su mobile 4G).

## 10.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| CMS.1 | Wagtail: site, locale, page type base, navigazione e footer per lingua | P1 | 8 | ARC.3 | Home e pagine statiche EN/IT |
| CMS.2 | Blocchi StreamField riusabili (hero, griglia prodotti, CTA, FAQ, chooser) | P1 | 10 | CMS.1, CAT.8 | Redattore compone la home senza codice |
| CMS.3 | Blog con schedulazione, autore, chooser prodotto/termine/vendor | P1 | 8 | CMS.2 | Articolo migrato pubblicato |
| CMS.4 | TermLandingPage e VendorLandingPage | P1 | 10 | CMS.2, CAT.8 | Pagina di un termine con i suoi prodotti |
| CMS.5 | LegalPage versionata + VendorRecruitmentPage con form candidatura | P1 | 6 | CMS.1, VEN.5 | Candidatura arriva in admin |
| CMS.6 | Recensioni: modello, verifica acquisto, import fornitori, moderazione, risposta vendor | P2 | 10 | ORD.2 | Schema AggregateRating solo con recensioni valide |
| CMS.7 | Tema frontend accessibile (WCAG 2.1 AA), componenti, design token | P1 | 24 | CMS.1 | Audit accessibilità superato |
| SEO.1 | JSON-LD Product/Offer/Breadcrumb/ReturnPolicy/ShippingDetails, robots, canonical | P1 | 10 | CAT.7, SHP.3 | Rich Results Test senza errori |
| SEO.2 | Sitemap per lingua e tipo, `llms.txt` generato | P1 | 4 | I18N.6 | Sitemap valide |
| SEO.3 | Feed Google (EN/IT) con ID legacy, prezzi dal motore fiscale, spedizioni da tabelle | P1 | 8 | TAX.3, SHP.11 | Feed validato in Merchant Center STAGE |
| SEO.4 | Sync Merchant API + feed Meta | P2 | 8 | SEO.3 | Report delta giornaliero |
| SEO.5 | Consenso cookie + Consent Mode v2 + eventi e-commerce | P1 | 8 | ORD.5 | Nessun tag prima del consenso |
| SEO.6 | Cache pagine anonime, rendition responsive, budget CWV | P2 | 6 | CMS.7 | LCP < 2,5 s su pagina prodotto |
| AI.1 | Server MCP: auth, tool di lettura (catalogo, ordini, preventivi, simulazioni, report) | P2 | 12 | CORE.7 | Tool usabili da Claude Code con scope |
| AI.2 | Tool MCP mutativi con dry-run, conferma e audit | P2 | 10 | AI.1 | Ogni mutazione ha diff e audit |
| AI.3 | API pubblica di sola lettura del catalogo per agenti | P3 | 6 | CAT.9 | Ricerca per termine di tassonomia via API |
| AI.4 | Valutazione protocolli di agent commerce (ACP, WebMCP) e prototipo | P3 | 12 | PAY.1, AI.3 | ADR con raccomandazione |
