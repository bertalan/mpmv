# 03 — Catalogo prodotti

## 3.1 Come funziona oggi

| Elemento | Implementazione WordPress |
|---|---|
| Tipi | `simple` e `variable` (`product_variation`); conversione via `convert_to_variable_product` (asse unico, es. `pa_subscription-duration`, o multiplo `pa_taglia` + `pa_colore`) |
| Lingue | Due post per prodotto (EN originale, IT traduzione) legati da `trid` WPML; SKU condiviso; l'IT a volte non ha attributi (fallback EN) |
| Categorie | `product_cat` gerarchica per tipo di prodotto (Categoria → Sottocategoria…), termini EN/IT gemelli |
| Tassonomia a due livelli | `product_tag` con term meta `_mp_tag_role` = livello 1 o livello 2; il livello 2 implica il livello 1; descrizioni SEO bilingui |
| Brand produttore | `product_brand` (marchi dei vendor…) → `g:brand` nel feed |
| Attributi | attributi globali `pa_*` (colore, materiale, finitura, taglia, tipo prodotto, provenienza, dimensioni, capacità, autore/ISBN per i libri…), alcuni con refusi storici nello slug |
| Prodotto complementare | un attributo `pa_*` contiene lo **SKU** di un complementare richiesto: relazione prodotto→prodotto codificata come attributo |
| Meta chiave | `_sku`, prezzi, stock, `_weight` e dimensioni, `_global_unique_id` (GTIN), `_wcfm_product_author`, `_mp_old_slugs`, `_wpsso_meta` (categoria Google), `_upsell_ids` |
| GPSR | **Assente** |

Funzioni di presentazione da mu-plugin:

- **Slug** `{titolo}-{lang}-{sku}` (max 80 caratteri, titolo troncato, traslitterazione), rigenerato
  a ogni salvataggio e cambio SKU, collisioni `-2`; ultimi 10 slug in `_mp_old_slugs` con 301 sui 404
  (`mp-sku-permalink.php`, 1.483 righe con console admin).
- **Redirect**: SKU ritirato → SKU sostitutivo o tag; tag uniti di recente; vecchie
  categorie dismesse → ricerca; `/author/*` → home; GET `?add-to-cart=` → scheda prodotto.
- **"Correlati"**: fino a 4 prodotti che condividono più termini di tassonomia, esclusi upsell
  e nascosti, ordinati per numero di match e poi a caso.
- **Box prodotto complementare** ("You might also need") da un attributo dedicato, con rollout per ID e posizione configurabile.
- **Variante di default**: preseleziona la default se acquistabile, altrimenti la più economica disponibile.
- **Ricerca per SKU**: prefisso ≥ 4 caratteri, anche SKU variante → padre, in OR col testo.
- **SKU nel carrello**, ordini ed email (`[SKU] Nome`).
- Link `?s=SKU` nelle descrizioni riscritti in link diretti al prodotto.
- Catalogo "solo su richiesta" (YITH Catalog Mode, vendor E): niente prezzo né add-to-cart.

## 3.2 Come funzionerà

### Modelli

```python
class Product(VendorScopedModel):
    vendor = FK(Vendor, on_delete=PROTECT)
    sku = CharField(64)                       # UNIQUE (vendor, sku); normalizzato maiuscolo
    kind = CharField(choices=["simple", "variable"])
    status = CharField(choices=["draft", "published", "retired"])  # retired = pagina viva, non acquistabile
    sales_mode = CharField(choices=["buy", "catalog_only", "on_request"])  # vendor E = on_request
    category = FK(Category)                   # categoria principale (+ M2M secondarie)
    brand = FK(Brand, null=True)              # produttore
    terms = M2M(Term)                         # tassonomie di navigazione; i livelli superiori si derivano
    fiscal_category = CharField(choices=["standard", "reduced", "book_art74"])
    price_net = MoneyField()                  # prezzi netti (come oggi: prices_include_tax=no)
    sale_price_net = MoneyField(null=True); sale_from/sale_to
    weight_kg = DecimalField(3)               # obbligatorio per "buy" (guida la spedizione)
    length_cm/width_cm/height_cm
    gtin = CharField(null=True)               # EAN/ISBN; altrimenti MPN = SKU
    google_category_id = IntegerField(null=True)
    required_companion = FK("self", null=True)  # ex attributo pa_*: complementare richiesto
    upsells = M2M("self")
    gpsr = OneToOne(GpsrInfo, null=True)      # vedi 11-compliance; blocca la pubblicazione se manca
    stock_policy = CharField(choices=["track", "always_open", "from_source"])
    legacy_wp_id_en / legacy_wp_id_it = IntegerField(null=True)   # g:id Merchant, redirect, audit

class ProductTranslation(Model):              # una riga per lingua, una sola fonte di verità per il resto
    product = FK(Product); language = CharField(choices=LANGUAGES)
    title; short_description; description (rich text)
    slug = SlugField()                        # derivato: {titolo}-{lang}-{sku}
    seo_title; seo_description
    status = CharField(choices=["machine", "reviewed", "published"])  # workflow traduzione
    source_hash                                # rileva quando l'EN cambia → IT "da rivedere"

class Variant(Model):
    product = FK(Product); sku (UNIQUE per vendor); options = M2M(AttributeValue)
    price_net_override; weight_kg_override; stock_qty; stock_status; is_default; position

class Attribute(Model):  code ("color", "material", "size", "bag_width"…); kind (choice|number|text); unit
class AttributeValue(Model): attribute; value; position;  + AttributeValueTranslation(label)
class ProductAttribute(Model): product; attribute; values = M2M; numeric_value; text_value

class Category(MP_Node):          # albero (treebeard), + CategoryTranslation(name, slug, seo, description)
class Taxonomy(Model):           # configurabile per verticale; + traduzione (nome, slug)
    code; hierarchical: bool; filterable: bool; has_landing: bool
    # es. "compatibilità" (marca → modello), "occasione", "tecnica", "stagione"
class Term(MP_Node):             # albero per tassonomia; + traduzione (slug, testo SEO)
    taxonomy = FK(Taxonomy); attributes (JSON, campi extra definiti dalla tassonomia)
class Brand(Model):               # produttore; + traduzione

class ProductImage(Model):
    product; variant (null); image = FK(wagtailimages.Image); position; is_cover; cover_locked
    # cover_locked: la copertina scelta a mano non viene sovrascritta dalle pipeline (regola attuale)

class SlugHistory(Model):  product; language; slug; replaced_at      # sostituisce _mp_old_slugs (illimitato)
class Redirect(Model):     source_path; target (path | product | term | search); code 301/410; expires_at
```

### Servizi principali

```python
# catalog/slugs.py — funzione pura, replica mp-sku-permalink
def build_product_slug(title: str, lang: str, sku: str, max_len: int = 80) -> str:
    """slugify(translit(title)) troncato + f"-{lang}-{sku.lower()}"; garantisce max_len."""

# catalog/services.py
def upsert_products(vendor, payload: list[ProductIn], *, dry_run: bool) -> ChangeSet
def publish_product(product, actor) -> None        # valida peso, prezzo, GPSR, almeno una traduzione
def retire_product(product, reason) -> None        # stock 0, pagina viva (regola SEO attuale)
def related_products(product, limit=4) -> list[Product]      # match sui terms condivisi, esclude upsell
def default_variant(product) -> Variant | None     # default se acquistabile, altrimenti più economica
def resolve_legacy_path(path: str) -> Redirect | None         # SKU ritirati, slug storici, categorie dismesse
def search(query: str, lang: str, filters) -> QuerySet        # FTS + trigram + prefisso SKU (≥4)
```

### URL (per lingua, prefisso `/it/`, `/de/`)

| Pagina | EN | IT |
|---|---|---|
| Prodotto | `/product/{slug}/` | `/it/prodotto/{slug}/` |
| Categoria | `/product-category/{path}/` | `/it/categoria-prodotto/{path}/` |
| Tassonomia (es. due livelli) | `/{taxonomy}/{term}/` e `/{taxonomy}/{term}/{child}/` | `/it/{tassonomia}/{termine}/…` (slug tradotti) |
| Brand produttore | `/brand/{slug}/` | `/it/marchio/{slug}/` |
| Negozio vendor | `/shop/{vendor}/` | `/it/shop/{vendor}/` |

Le URL prodotto e categoria **restano identiche** a quelle attuali, così nessun redirect è
necessario per la maggior parte del catalogo. I tag `/tag-product/{slug}/` e
`/it/tag-product-it/{slug}-it/` vengono reindirizzati con 301 alle nuove pagine di tassonomia (decisione D-07).

### Regole

- Un prodotto `buy` non si pubblica senza peso > 0, prezzo > 0, almeno la traduzione EN, dati GPSR.
- Il cambio di SKU o titolo rigenera lo slug e scrive `SlugHistory`; il vecchio slug risponde 301.
- `retired`: la pagina resta online con "non più disponibile" e alternative simili, mai 404
  (regola SEO attuale del ritiro prodotti).
- Lo stock negativo è impossibile (vincolo DB); backorder solo se `stock_policy=always_open`.

## 3.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| CAT.1 | Modelli Product, Variant, traduzioni, vincoli `(vendor, sku)` | P1 | 12 | ARC.6, VEN.1 | Migrazioni + test vincoli |
| CAT.2 | Tassonomie: Category (albero), Taxonomy/Term configurabili, Brand, traduzioni e testi SEO | P1 | 10 | CAT.1 | Admin CRUD con traduzioni |
| CAT.3 | Attributi tipizzati e valori tradotti; assi delle varianti con ordinamento | P1 | 10 | CAT.1 | Prodotto variabile taglia×colore creato da API |
| CAT.4 | Media: ProductImage su Wagtail Images, rendition WebP/AVIF, dedup per hash, cover lock | P1 | 10 | CAT.1 | Upload → rendition automatiche |
| CAT.5 | `build_product_slug` + SlugHistory + middleware redirect 301 | P1 | 8 | CAT.1 | Casi di test portati da mp-sku-permalink |
| CAT.6 | Modello `Redirect` + gestione admin + import mappe esistenti (SKU ritirati, tag uniti, categorie dismesse) | P1 | 6 | CAT.5 | Tutte le regole dei 4 mu-plugin coperte da test |
| CAT.7 | Scheda prodotto: galleria, varianti con default, box complementare, correlati, SKU visibile, "su richiesta" | P1 | 16 | CAT.3, CAT.4 | Parità visiva e funzionale con LIVE |
| CAT.8 | Liste: categoria, pagina di tassonomia, brand, negozio vendor, filtri (categoria, termine di tassonomia, prezzo, vendor) | P1 | 14 | CAT.2 | Paginazione e filtri con URL indicizzabili |
| CAT.9 | Ricerca FTS + trigram + prefisso SKU (anche variante) | P1 | 8 | CAT.1 | Test su SKU parziali e refusi |
| CAT.10 | Servizi `upsert_products`/`publish`/`retire` con dry-run e regole di pubblicazione | P1 | 10 | CAT.1, CMP.3 | ChangeSet verificato in test |
| CAT.11 | Admin catalogo (Wagtail ViewSet): filtri, bulk, anteprima, stato traduzioni | P2 | 10 | CORE.8 | Staff modifica prodotti senza API |
