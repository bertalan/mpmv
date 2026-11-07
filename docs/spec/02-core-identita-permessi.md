# 02 — Identità, ruoli, isolamento vendor, API e backoffice

## 2.1 Come funziona oggi

- Un unico login WordPress per clienti, vendor e staff; un unico `/wp-admin` per tutti i ruoli.
- I vendor WCFM hanno la capability `manage_woocommerce`: ogni controllo "admin" deve usare
  `manage_options` (bug documentato nel plugin IVA).
- Staff e gruppi WCFM non configurati; l'unico uso è il filtro `wcfmgs_user_capability` per
  il vendor E, che si è rotto con l'aggiornamento WCFM.
- API: `api.php` con route incluse dinamicamente da `routes/{route}.php`; il modello di
  autenticazione va rifatto (dettagli nel documento riservato).
- Supporto clienti con "Login as User".

## 2.2 Come funzionerà

### Modelli

```python
class User(AbstractUser):            # accounts — custom fin dal primo migrate
    email = EmailField(unique=True)  # login via email
    preferred_language = CharField(choices=LANGUAGES, default="en")
    is_staff_member: bool            # staff della piattaforma (backoffice)
    mfa_enabled: bool

class VendorMember(Model):           # vendors — appartenenza utente ↔ vendor
    vendor = FK(Vendor); user = FK(User)
    role = CharField(choices=["owner", "staff"])
    # owner: payout, IBAN, regime IVA, utenti; staff: ordini, preventivi, prodotti

class ApiClient(Model):              # api — credenziale macchina (pipeline, MCP, integrazioni)
    name; vendor = FK(Vendor, null=True)      # null = client di piattaforma
    scopes = ArrayField(CharField())          # es. ["catalog:write", "stock:write"]
    key_prefix = CharField(8); key_hash = CharField()   # mai la chiave in chiaro
    expires_at; last_used_at; allowed_ips = ArrayField(InetAddress, null=True)
    is_active: bool
```

### Ruoli

| Ruolo | Dove opera | Può |
|---|---|---|
| Anonimo | Sito | Navigare, carrello, checkout ospite, richiesta preventivo |
| Cliente | Area cliente | Ordini, preventivi, resi/recesso, dati personali, export GDPR |
| Vendor owner | Dashboard vendor | Tutto ciò che riguarda **il proprio** vendor, incluse impostazioni economiche |
| Vendor staff | Dashboard vendor | Ordini, preventivi, prodotti; niente payout, IBAN o regime fiscale |
| Redattore | Wagtail admin | Pagine, blog, testi SEO, traduzioni |
| Operatore staff | Wagtail admin + admin commerce | Vendor, catalogo, ordini, preventivi, zone, aliquote |
| Superuser tecnico | Django admin (rete ristretta) | Manutenzione; uso eccezionale, tracciato |

### Isolamento tenant (vendor)

Un unico meccanismo, usato ovunque (view, API, MCP, export, ricerca admin):

```python
class VendorScopedQuerySet(QuerySet):
    def for_actor(self, actor: Actor) -> "VendorScopedQuerySet":
        """Staff: tutto. Vendor member: solo i propri record. Altri: nulla."""

def get_for_actor_or_404(qs, actor, **lookup):  # 404 (non 403) per evitare enumeration
```

- Ogni modello con `vendor` FK usa questo manager.
- **Test negativi obbligatori** per ogni endpoint: "vendor A chiede l'oggetto di vendor B → 404".
- I documenti vendor (KYC, fatture) si scaricano solo da view che verificano i permessi; mai URL
  pubblici.

### Autenticazione

- Sessione web: password moderna (controllo contro password compromesse), **MFA TOTP/passkey
  obbligatoria** per staff e vendor owner, blocco progressivo per account e IP.
- Step-up (ri-autenticazione) per: cambio IBAN, payout, modifica aliquote, export dati clienti.
- API: header `Authorization: Bearer piac_<prefix>_<secret>`, confronto a tempo costante,
  ambito verificato per route, rate limit per client, log di ogni chiamata mutativa.
- Impersonazione per il supporto (equivalente di Login as User), solo staff, con audit.

### API v1 (sostituisce `api.php`)

Le ~85 route "ricorrenti" si accorpano in ~15 risorse REST:

| Risorsa | Operazioni | Sostituisce |
|---|---|---|
| `/products` | upsert bulk per `(vendor, sku)`, get, export paginato, ritiro | `create_product_full`, `update_product_*_relationships`, `get_all_product_*`, `export_vendor_products`, `delete_product`, `trash_products` |
| `/products/{sku}/variants` | upsert varianti, assi, default | `convert_to_variable_product` |
| `/stock`, `/prices` | aggiornamento bulk con soglie | `batch_update_stock`, `get_vendor_product_stock/prices` |
| `/media` | upload diretto (presigned URL), assegnazione cover/galleria | `upload_media`, `upload_images_from_folder`, `convert_images_webp`, `regenerate_product_thumbnails` |
| `/taxonomy/{categories,taxonomies,terms,brands}` | CRUD con traduzioni e testi SEO | `create_categories`, `create_tags`, `*_tag_translations`, `update_*_descriptions`, `assign_*` |
| `/taxonomy/assign-by-content` | assegnazione per parole chiave con dry-run | `assign_brand_tags_by_content` |
| `/shipping/config`, `/shipping/simulate` | lettura/scrittura validata, simulazione | `*_custom_shipping_vendors_config*`, `manage_gls_shipping_zones`, `test` |
| `/tax/rates`, `/tax/simulate` | aliquote con validità, simulazione carrello | `sync_eu_standard_vat_rates`, `simulate_cart_tax` |
| `/vendors/{id}` | profilo, policy, logo (per lingua) | `update_vendor_*` |
| `/reviews` | import recensioni fornitore | `manage_product_reviews` |
| `/content/posts` | bozze blog | `create_post`, `update_post` |

Regole comuni: ogni POST mutativo accetta `?dry_run=true` e restituisce il diff; le operazioni
bulk sono idempotenti (stessa richiesta due volte = stesso stato); errori in formato RFC 9457.
Le route diagnostiche e di fix (`diagnose_*`, `fix_*`, `debug_*`) **non si migrano**: diventano
report in admin o tool MCP di sola lettura.

### Backoffice

- **Wagtail admin** per staff e redattori: pagine, immagini, snippet; `ModelViewSet` per vendor,
  prodotti, zone, aliquote, ordini e preventivi, con filtri e azioni bulk.
- **Dashboard vendor** come area separata (`/vendor/`), template HTMX, viste filtrate per tenant:
  ordini, inserimento preventivi, prodotti (modifica limitata), payout, richiesta di modifica
  del regime fiscale.
- **Django admin** solo per il superuser tecnico, raggiungibile da rete ristretta.
- **Server MCP** (vedi [10](10-contenuti-seo-ai.md)): tool sopra gli stessi servizi.

## 2.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| CORE.1 | `User` custom, login via email, lingua preferita | P1 | 6 | ARC.6 | Migrazione iniziale con User custom |
| CORE.2 | `VendorMember` con ruoli owner/staff, permessi per ruolo | P1 | 6 | CORE.1, VEN.1 | Matrice ruoli testata |
| CORE.3 | `VendorScopedQuerySet` + helper 404 + fixture di test cross-tenant riusabile | P1 | 8 | CORE.2 | Test negativi generati per ogni endpoint |
| CORE.4 | MFA (TOTP + passkey) obbligatoria per staff/owner, recovery code, step-up | P1 | 12 | CORE.1 | Login senza MFA impossibile per ruoli obbligati |
| CORE.5 | Rate limit e blocco progressivo login/registrazione, Turnstile | P1 | 6 | CORE.1 | Test su tentativi falliti |
| CORE.6 | `ApiClient`: emissione, hash, scope, scadenza, rotazione, revoca, allow-list IP | P1 | 10 | CORE.1 | Chiave scaduta o fuori scope → 401/403 |
| CORE.7 | Scheletro API ninja v1: auth, dry-run, errori RFC 9457, OpenAPI, log mutazioni | P1 | 10 | CORE.6 | Spec OpenAPI pubblicata in STAGE |
| CORE.8 | Wagtail admin: menu staff, ViewSet base con filtri tenant e azioni bulk | P1 | 10 | CORE.3 | Staff gestisce vendor e prodotti da admin |
| CORE.9 | Area vendor `/vendor/`: layout, navigazione, dashboard KPI essenziali | P1 | 12 | CORE.3 | Vendor vede solo i propri dati |
| CORE.10 | Impersonazione per supporto con audit | P2 | 4 | CORE.8 | Ogni sessione impersonata in AuditLog |
