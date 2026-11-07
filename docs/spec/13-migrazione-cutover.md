# 13 — Migrazione dati e cutover

## 13.1 Strategia

Alla scala attuale (pochi vendor, migliaia di prodotti per lingua, decine di ordini al giorno) la soluzione
più economica è un **cutover unico e provato più volte**, non un doppio binario prolungato.

1. ETL ripetibile e idempotente da un dump LIVE (in ambiente protetto) verso il nuovo schema.
2. Report di parità automatici a ogni esecuzione.
3. Almeno **tre prove generali** su STAGE (con dati anonimizzati per i clienti).
4. Freeze breve di WordPress, ultima esecuzione, switch DNS, smoke test, riapertura.
5. WordPress resta in **sola lettura** per 2–4 settimane come rete di sicurezza.

## 13.2 Cosa si migra e come

| Dato | Sorgente WP | Trasformazione | Note |
|---|---|---|---|
| Vendor | user meta WCFM, VTO, Stripe | → `Vendor`, traduzioni policy, `VendorTaxProfile`, `PayoutAccount`, `CommissionRule` | Gli account Stripe Connect (`acct_…`) **si riusano** se resta lo stesso account piattaforma: nessun nuovo onboarding KYC |
| Prodotti | post EN + IT legati da `trid`, meta, attributi `pa_*` | → un `Product` + `ProductTranslation` EN/IT; varianti; attributi tipizzati | Chiave: SKU + `trid`; `legacy_wp_id_en/it` conservati |
| SKU duplicati | 29 casi nel PostgreSQL della pipeline, `-2` e triplicati su WP | Report → decisione manuale (tieni / unisci / ritira) prima del caricamento | Il vincolo `(vendor, sku)` non ammette eccezioni |
| Categorie | `product_cat` EN/IT | → `Category` + traduzioni; correzione dei prodotti IT su categorie EN per costruzione | |
| Tag a due livelli | `product_tag` con `_mp_tag_role` | → `Taxonomy` / `Term` + testi SEO | I tag senza ruolo vanno classificati prima |
| Attributo complementare | SKU del complementare | → `Product.required_companion` (FK) | SKU non trovati in report |
| Brand | `product_brand` | → `Brand` | |
| Immagini | Media Library | → object storage, dedup per hash, cover e galleria, alt per lingua | Rendition rigenerate |
| Slug storici | `_mp_old_slugs`, mappe dei mu-plugin di redirect | → `SlugHistory` + `Redirect` | |
| Clienti | utenti WP con ruolo customer | → `User`; hash password WordPress accettati al primo login e poi convertiti | Hasher custom per phpass e per il formato bcrypt di WP 6.8+ |
| Ordini storici | `shop_order` + `wcfm_marketplace_orders` | → `Order`/`VendorOrder`/`OrderLine` **in sola lettura** con flag `legacy` | Nessun ricalcolo fiscale retroattivo |
| Preventivi aperti | meta `_mp_quote_*` | Chiudere prima del cutover, oppure migrare lo stato | Decisione nel runbook |
| Ledger vendor | `wcfm_vendor_ledger`, prelievi | → saldo di apertura per vendor (una riga `adjustment` documentata) | Verificato con la riconciliazione Stripe |
| Recensioni | commenti prodotto | → recensioni con origine | |
| Pagine e blog | post e pagine (Elementor) | Migrazione **semi-manuale**: testi estratti, impaginazione ricostruita con StreamField | L'HTML Elementor non si converte bene in automatico |
| Testi SEO | `_wpsso_meta`, descrizioni di tag e categorie | → campi SEO delle traduzioni | |
| Zone di spedizione | option `custom_shipping_vendors_config_v2` | → modelli `shipping` (vedi SHP.5) | |
| Feed Merchant | `g:id` = ID post | `legacy_wp_id` usato come id nel nuovo feed | Continuità dello storico Merchant Center |

## 13.3 Report di parità (automatici)

- Conteggi per vendor, lingua, categoria, termine di tassonomia, stato.
- Campione di 100 prodotti confrontati campo per campo (prezzo, stock, peso, attributi, immagini, testi).
- Prezzo IVA inclusa per 50 combinazioni prodotto × paese: WP contro motore fiscale nuovo.
- Tariffe di spedizione per 200 combinazioni vendor × paese × CAP × peso.
- **Tutti** gli URL pubblici EN/IT di WP (dalla sitemap) → risposta 200 o 301 verso una pagina 200.

## 13.4 Runbook di cutover

1. T−7 giorni: TTL DNS ridotto; preventivi aperti chiusi o pianificati; comunicazione ai vendor.
2. T−1: ultima prova generale; via libera (go/no-go) sui report di parità.
3. T0: freeze di WordPress (manutenzione, niente checkout); ultimo ETL; report di parità.
4. Switch DNS; smoke test: navigazione EN/IT, ricerca, carrello multi-vendor, pagamento reale di
   piccolo importo con rimborso, preventivo, dashboard vendor, feed.
5. Riapertura; monitoraggio intensivo 72 h (404, errori, conversioni, webhook).
6. Rollback: DNS verso WordPress (rimasto intatto) se un criterio di no-go si verifica nelle prime 24 h;
   gli ordini nel frattempo raccolti sul nuovo sistema si gestiscono manualmente.
7. T+14/28: Merchant Center e Search Console verificati, WordPress spento e archiviato.

## 13.5 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| MIG.1 | Estrattore dal DB WP (`kvsa_`) in formato intermedio JSON, con inventario dei meta | P1 | 10 | — | Inventario completo dei campi usati |
| MIG.2 | Report SKU duplicati, tag senza ruolo, complementari orfani → decisioni | P1 | 6 | MIG.1 | Lista decisioni approvata |
| MIG.3 | Loader vendor, catalogo, tassonomie, immagini (idempotente, dry-run) | P1 | 16 | MIG.1, CAT.1, CAT.2, CAT.3, CAT.4 | Ricaricamento senza duplicati |
| MIG.4 | Loader clienti (hasher password WP) e ordini storici in sola lettura | P1 | 10 | MIG.1, ORD.2 | Cliente migrato accede con la vecchia password |
| MIG.5 | Report di parità automatici (§13.3) | P1 | 10 | MIG.3 | Report generato a ogni esecuzione |
| MIG.6 | Mappa redirect completa EN/IT + verifica automatica di tutti gli URL della sitemap WP | P1 | 6 | CAT.6, MIG.3 | 100% degli URL 200/301 |
| MIG.7 | Migrazione pagine e blog (semi-manuale) con StreamField | P1 | 16 | CMS.2 | Tutte le pagine pubbliche presenti |
| MIG.8 | Saldi di apertura ledger vendor + verifica con Stripe | P1 | 4 | VEN.4, MIG.3 | Saldo = atteso per ogni vendor |
| MIG.9 | Tre prove generali su STAGE + runbook di cutover e rollback | P1 | 16 | MIG.5, MIG.6 | Terza prova senza blocchi |
| MIG.10 | Cutover e monitoraggio post go-live | P1 | 12 | MIG.9, SEC.6 | Go-live, 72 h senza criticità |
