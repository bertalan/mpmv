# 08 — Import, pipeline fornitori e sincronizzazioni

## 8.1 Come funziona oggi

Le pipeline sono già in Python e girano sul server DEV con un PostgreSQL
dedicato; pubblicano su WordPress tramite le route PHP di `api.php`.

**Vendor A** (cartella pipeline dedicata, manuale esteso, 35 file pytest):

| Step | Script / classe | Cosa fa |
|---|---|---|
| 1–3 | `1_link_category.py` (`Scraper`), `2_link_subcategory.py`, `3_link_product.py` | Crawling categorie → sottocategorie → URL prodotto |
| 4 | `4_get_product.py` o `4_get_product_api.py` (`StoreApiClient`, `product_data_from_store_api`) | Scheda prodotto da HTML o da **Store API WooCommerce** del fornitore; storico append-only `single_product_change`; 404 in `supplier_source_missing` |
| 5 | `5_product_extract.py` (`EXTRACTproduct`), `extract_utils.py` | Estrazione deterministica (regex + mapping JSON): tipo, modello, materiali, dimensioni, capacità, peso, colore, `get_unique_sku` |
| 6 | `6_product_for_sync.py` (`ProductRelations`), `gemini_client.py` | Record di staging con testi EN/IT generati da **Gemini** (policy 650–850 caratteri, "answer-first"), attributi, categorie e tag, prezzi |
| 7 | `7_scrape_reviews.py` | Recensioni del fornitore |
| 9 | `9_upload_images.py` | rsync + route `upload_images_from_folder` + assegnazione cover/galleria (cover manuale protetta) |
| z3 | `z3_send_product_to_wp.py` (`WPProductUploader`) | Crea EN poi IT come traduzione WPML; ID WP in `wordpress_product_sync` |
| 10 | `10_stage_to_live_sync.py` | Promozione STAGE → LIVE (flag, media, clone cross-DB, fix WCFM) |
| 11–13, z4 | `StockSyncer`, `PriceSyncer`, image refresh, ritiro prodotti | Job ricorrenti post-pubblicazione |

Moduli di supporto riusabili: `supplier_source_state.py` (identità = URL fornitore, osservazioni per
variante), `product_change_utility.py`, `stock_state.py` (fail-closed), `supplier_variations.py`,
`image_utility.py` (WebP, EXIF, lato minimo 1280), `content_archive.py` (versioni dei testi AI con
hash), `daily_report.py` + `report_mailer.py`, `execution_safety.py` (gate di scrittura, dry-run di
default), mapping JSON (`product_type_map`, `colors_map`, `materials_map`, categorie e 164 tag).

**Altri vendor**: vendor B (listino XLSX master di prezzi e disponibilità; SKU fuori listino →
esaurito), vendor D (script di import e sync con speed-check), vendor E via la pipeline "light" (`codice_vendor_light/`)
(`source.yaml` per SKU → `enrich_product.py` con Gemini → schede PDF di revisione → payload in dry-run).

**Regole di business codificate** (da mantenere):

- Il fornitore comanda lo stock: quantità esatta; 404 → stock 0, pagina pubblicata (SEO).
- Dati non freschi: segnalati, mai applicati.
- Variazioni di prezzo oltre il 15% e prodotti in saldo → revisione; prezzo netto = lordo fornitore / 1,22.
- Cambi d'identità del fornitore (SKU o modello cambiati, pagine `-copy`) → solo segnalati nel report.

## 8.2 Come funzionerà

Le pipeline entrano nel progetto come app `ingest`: stessa codebase, stesso DB, **nessun passaggio
HTTP verso PHP**, nessuna promozione STAGE → LIVE. I job girano nel worker (container dedicato,
separabile su altra macchina se lo scraping lo richiede). L'API `/api/v1` resta per i vendor che
sincronizzano da soli (piano avanzato) e per eventuali pipeline esterne.

### Struttura

```
apps/ingest/
  adapters/
    base.py            # SupplierAdapter (Protocol)
    html_scraper.py    # step 1–4 attuali (vendor A)
    woo_store_api.py   # generico: qualsiasi fornitore WooCommerce (da 4_get_product_api.py)
    xlsx_pricelist.py  # vendor B
    publisher_feed.py  # vendor D
    yaml_light.py      # vendor senza sito (vendor E): content/vendors/<vendor>/prodotti/<SKU>/source.yaml
    csv_generic.py     # vendor con export CSV
  extract/             # 5_product_extract.py, extract_utils.py, attribute_builder.py, mapping JSON
  enrich/              # GeminiClient dietro TextEnricher, prompt e policy, content_archive
  images.py            # image_utility.py
  state.py             # supplier_source_state, product_change_utility, stock_state, missing/image state
  publish.py           # da staging a catalogo via catalog.services.upsert_products
  jobs.py              # task schedulati
  reports.py           # daily_report, report_mailer
```

### Modelli di staging

```python
class SupplierSource(Model):       vendor; adapter_code; config (JSON); schedule; is_active
class SourceItem(Model):           source; source_url (identità); aliases; product (FK, null); last_seen_at; missing_since
class SourceObservation(Model):    item; observed_at; payload (JSON); payload_hash   # append-only, niente versioni duplicate
class ProductDraft(Model):         item; extracted (JSON); enriched (JSON per lingua); status (new|ready|needs_review|published|rejected)
class ImportRun(Model):            source; kind (scrape|stock|price|images|publish); started/finished; dry_run; counters; log
class ReviewItem(Model):           run; product; kind (price_jump|sale|identity_change|stale|duplicate_sku); payload; resolved_by
class MappingTable(Model):         vendor; kind (category|term|color|material…); key; target (FK generica); aliases
```

Le mappe JSON attuali diventano `MappingTable` con **FK** a categorie e termini di tassonomia (non più ID
WordPress per lingua), modificabili da admin e versionate.

### Contratti

```python
class SupplierAdapter(Protocol):
    code: str
    def discover(self, source: SupplierSource) -> Iterable[SourceRef]
    def fetch(self, ref: SourceRef) -> RawObservation          # HTML, JSON, riga XLSX, YAML
    def extract(self, raw: RawObservation) -> ExtractedProduct # Pydantic, validato

class TextEnricher(Protocol):                                   # provider AI intercambiabile
    def describe(self, product: ExtractedProduct, lang: str, policy: TextPolicy) -> EnrichedText
    def translate(self, text: EnrichedText, target: str) -> EnrichedText

def plan_stock_actions(observations, products) -> list[StockAction]   # funzione pura (da StockSyncer)
def plan_price_actions(observations, products, max_change_pct=15) -> list[PriceAction]  # da compute_price_actions
def publish_drafts(drafts, *, dry_run=True) -> ChangeSet                 # chiama catalog.services
```

Regole: dry-run di default; il passaggio a scrittura richiede un flag per sorgente (sostituisce i
file marker di `execution_safety.py`); l'output AI è validato contro schema e policy prima di
diventare bozza; le traduzioni generate hanno stato `machine` e richiedono revisione se la policy del
vendor lo prevede; l'unicità `(vendor, sku)` è garantita dal DB (i 29 SKU duplicati attuali si
risolvono in migrazione, vedi [13](13-migrazione-cutover.md)).

### Job ricorrenti

| Job | Orario attuale | Nel nuovo sistema |
|---|---|---|
| Scraping vendor A + registro 404 e cambi foto | ~06:20 UTC | `ingest.scrape` per sorgente |
| Stock sync (varianti incluse) | 07:30 UTC, prima STAGE poi LIVE | `ingest.stock` subito dopo lo scraping, una sola destinazione |
| Price sync con soglia di revisione | manuale/cron | `ingest.price` → `ReviewItem` oltre soglia |
| Listino vendor B da XLSX | manuale | upload in admin o cartella monitorata → `ingest.pricelist` |
| Speed-check vendor D | cron | `ingest.speed_check` |
| Image refresh sulle foto cambiate | cron | `ingest.images` (hash, rendition automatiche) |
| Ritiro prodotti spariti | z4 | parte di `ingest.stock` (stato `retired`) |
| Import recensioni | cron | `ingest.reviews` |
| Feed Google Merchant EN/IT | giornaliero | `seo.merchant_sync` (vedi [10](10-contenuti-seo-ai.md)) |
| Report giornaliero email | 23:10 UTC | `ingest.daily_report` + pagina admin |
| Backup contenuti AI | ~22:50 UTC | non serve: i testi sono versionati nel DB (backup DB) |
| Upload testi tag a lotti di 5 | mu-plugin cron | import una tantum in migrazione |
| "Live drip" nuovi prodotti (~10/giorno) | in pausa | pubblicazione programmata delle bozze `ready` |

### Onboarding di un nuovo vendor (procedura standard)

1. Scegliere l'adapter (Store API WooCommerce, scraping, XLSX/CSV, YAML light).
2. Configurare la `SupplierSource` e le `MappingTable` (categorie, termini di tassonomia, colori, materiali).
3. Primo run in dry-run → report con bozze, mapping mancanti, campi GPSR mancanti.
4. Revisione delle bozze (testi AI, immagini) → pubblicazione programmata.
5. Attivazione dei job ricorrenti per stock e prezzi.

Obiettivo: un vendor con sito WooCommerce si attiva **senza scrivere codice**.

## 8.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| ING.1 | App `ingest`: modelli di staging, ImportRun, ReviewItem, MappingTable | P1 | 10 | CAT.1 | Migrazioni + admin |
| ING.2 | Port di `state.py` (source state, change history, stock state) sui modelli Django + test esistenti | P1 | 12 | ING.1 | I pytest attuali passano sul nuovo storage |
| ING.3 | Adapter HTML (vendor A) + Store API generico | P1 | 12 | ING.2 | Run in dry-run equivalente a quello attuale |
| ING.4 | Port estrazione (step 5) e mapping JSON → MappingTable con FK | P1 | 10 | ING.1, CAT.2 | Stesso output dello step 5 su 50 prodotti campione |
| ING.5 | `TextEnricher` (Gemini) con policy, validazione schema, archivio versioni | P1 | 8 | ING.1 | Testi fuori policy finiscono in revisione |
| ING.6 | Immagini: pipeline hash → storage → rendition, protezione cover | P1 | 6 | CAT.4 | Foto cambiate rilevate per hash |
| ING.7 | `publish_drafts` via `catalog.services` con dry-run e pubblicazione programmata | P1 | 8 | CAT.10 | Bozza → prodotto EN/IT in un passo |
| ING.8 | `plan_stock_actions` / `plan_price_actions` + job e ReviewItem | P1 | 10 | ING.2 | Regole fornitore (404, stale, >15%, saldi) verdi |
| ING.9 | Adapter XLSX (vendor B), feed editore (vendor D), YAML light (vendor E), CSV generico | P1 | 16 | ING.4 | Un run dry-run per ciascun vendor |
| ING.10 | Report giornaliero (email + pagina admin) con anomalie d'identità | P2 | 6 | ING.8 | Parità con `daily_report.py` |
| ING.11 | Import recensioni fornitore | P2 | 4 | ING.1, CMS.6 | Recensioni importate con data originale |
| ING.12 | API `/api/v1` di import per vendor esterni (upsert, stock, prezzi, media) | P2 | 8 | CORE.7, CAT.10 | Client di esempio in dry-run |
| ING.13 | Checklist e wizard admin "nuovo vendor da sorgente" | P2 | 8 | ING.9 | Vendor demo attivato senza codice |
