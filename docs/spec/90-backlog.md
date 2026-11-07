# 90 — Backlog (generato)

> File generato da `tools/build_docs.py` a partire dalle tabelle dei task nei documenti di
> dominio. **Non modificarlo a mano**: si modifica il documento di dominio e si rigenera.
> Il CSV per Jira/Linear/GitHub Projects è in [backlog.csv](backlog.csv).

## Riepilogo

- Micro-task: **141** in **17** macro-aree
- Ore stimate (sviluppo + test automatici, sviluppo AI-assistito): **1230 h** (P1 1023 h · P2 189 h · P3 18 h)
- Percorso critico P1 (limite inferiore del calendario con parallelismo pieno): **126 h** — ARC.1 → ARC.3 → ARC.6 → VEN.1 → CAT.1 → ORD.1 → ORD.2 → ORD.4 → PAY.1 → PAY.2 → PAY.3 → PAY.4 → ORD.8

## Per macro-area

| Macro | Area | Task | P1 h | P2 h | P3 h | Totale h |
|---|---|---:|---:|---:|---:|---:|
| ARC | Architettura e fondamenta | 10 | 68 | 6 | 0 | 74 |
| CORE | Identità, permessi, API, backoffice | 10 | 80 | 4 | 0 | 84 |
| CAT | Catalogo prodotti | 11 | 104 | 10 | 0 | 114 |
| VEN | Vendor, commissioni, ledger | 11 | 80 | 14 | 0 | 94 |
| TAX | Motore fiscale | 10 | 42 | 28 | 0 | 70 |
| SHP | Spedizioni e preventivi | 11 | 64 | 28 | 0 | 92 |
| ORD | Carrello, ordini, resi | 10 | 102 | 0 | 0 | 102 |
| PAY | Pagamenti (Stripe Connect) | 4 | 40 | 0 | 0 | 40 |
| ING | Import e pipeline fornitori | 13 | 92 | 26 | 0 | 118 |
| I18N | Multilingua | 6 | 24 | 8 | 0 | 32 |
| CMS | Contenuti Wagtail | 7 | 66 | 10 | 0 | 76 |
| SEO | SEO, feed, analytics | 6 | 30 | 14 | 0 | 44 |
| AI | Canali AI (MCP, agenti) | 4 | 0 | 22 | 18 | 40 |
| CMP | Compliance | 7 | 58 | 8 | 0 | 66 |
| SEC | Sicurezza | 7 | 51 | 0 | 0 | 51 |
| OPS | Operatività | 4 | 16 | 11 | 0 | 27 |
| MIG | Migrazione e cutover | 10 | 106 | 0 | 0 | 106 |
| **Totale** | | **141** | **1023** | **189** | **18** | **1230** |

## Per fase

### F0 — Fondamenta (25 task, 189 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| ARC.1 | ADR-0001 stack (`docs/adr/`) approvato, versioni fissate | P1 | 4 | — | ADR firmato nel repo |
| ARC.2 | ADR-0003 confini del commerce custom (cosa non si scrive in casa); ADR-0004 coda task (django-tasks vs Procrastinate) | P1 | 4 | ARC.1 | ADR nel repo |
| ARC.3 | Bootstrap repo: struttura app, settings per ambiente, env, pre-commit, Makefile | P1 | 8 | ARC.1 | `make check` e `make test` verdi in locale |
| ARC.4 | CI: ruff, mypy, pytest, coverage, import-linter (grafo dipendenze), pip-audit, secret scan | P1 | 8 | ARC.3 | Pipeline verde su PR |
| ARC.5 | Container: `Containerfile` OCI, stack locale con Apple `container` (rete, volumi, app, worker, Postgres), runtime Linux per STAGE/LIVE, immagini versionate | P1 | 10 | ARC.3 | `make dev` con `container`; deploy STAGE da tag con rollback |
| ARC.6 | `core`: base model, `MoneyField`, Country, `ChangeSet`/dry-run, `AuditLog` | P1 | 12 | ARC.3 | Test su arrotondamenti e audit |
| ARC.7 | `PlatformOperator` (paese, P.IVA, regime, canale e-invoice, autorità DAC7) come config | P1 | 4 | ARC.6 | Nessuna costante "IT" nel codice fiscale |
| ARC.9 | Worker task + scheduler cron su Postgres, con lock e retry | P1 | 8 | ARC.2 | Job di prova schedulato e osservabile |
| CORE.1 | `User` custom, login via email, lingua preferita | P1 | 6 | ARC.6 | Migrazione iniziale con User custom |
| CORE.2 | `VendorMember` con ruoli owner/staff, permessi per ruolo | P1 | 6 | CORE.1, VEN.1 | Matrice ruoli testata |
| CORE.3 | `VendorScopedQuerySet` + helper 404 + fixture di test cross-tenant riusabile | P1 | 8 | CORE.2 | Test negativi generati per ogni endpoint |
| CORE.4 | MFA (TOTP + passkey) obbligatoria per staff/owner, recovery code, step-up | P1 | 12 | CORE.1 | Login senza MFA impossibile per ruoli obbligati |
| CORE.5 | Rate limit e blocco progressivo login/registrazione, Turnstile | P1 | 6 | CORE.1 | Test su tentativi falliti |
| CORE.6 | `ApiClient`: emissione, hash, scope, scadenza, rotazione, revoca, allow-list IP | P1 | 10 | CORE.1 | Chiave scaduta o fuori scope → 401/403 |
| CORE.7 | Scheletro API ninja v1: auth, dry-run, errori RFC 9457, OpenAPI, log mutazioni | P1 | 10 | CORE.6 | Spec OpenAPI pubblicata in STAGE |
| CORE.8 | Wagtail admin: menu staff, ViewSet base con filtri tenant e azioni bulk | P1 | 10 | CORE.3 | Staff gestisce vendor e prodotti da admin |
| CORE.9 | Area vendor `/vendor/`: layout, navigazione, dashboard KPI essenziali | P1 | 12 | CORE.3 | Vendor vede solo i propri dati |
| SEC.0 | Azioni immediate del §12.1 sul sistema attuale | P1 | 3 | — | Credenziali ruotate, file ripuliti |
| SEC.1 | Hardening HTTP + CSP + cookie + header (test automatici) | P1 | 6 | ARC.3 | Scansione header superata |
| SEC.2 | Upload sicuri e ri-encoding immagini | P1 | 4 | CAT.4 | Test con file malevoli |
| SEC.3 | Cifratura di campo (IBAN, KYC, DAC7) con rotazione chiave | P1 | 8 | VEN.1 | Dump DB senza dati in chiaro |
| SEC.4 | AuditLog esteso a tutte le mutazioni sensibili + vista staff con filtri | P1 | 6 | ARC.6 | Modifica aliquota visibile con prima/dopo |
| SEC.5 | Test di sicurezza in CI (permessi per ruolo e tenant, endpoint senza auth) | P1 | 8 | CORE.3 | Endpoint pubblico non dichiarato → CI rossa |
| OPS.1 | Backup PITR + dump cifrati off-site + prova di ripristino | P1 | 8 | ARC.5 | Ripristino documentato su ambiente vuoto |
| OPS.2 | Osservabilità: log JSON, error tracking, healthcheck, alert | P1 | 8 | ARC.5 | Alert su errore di checkout simulato |

### F1 — Catalogo, import, frontend base (26 task, 252 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
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
| ING.1 | App `ingest`: modelli di staging, ImportRun, ReviewItem, MappingTable | P1 | 10 | CAT.1 | Migrazioni + admin |
| ING.2 | Port di `state.py` (source state, change history, stock state) sui modelli Django + test esistenti | P1 | 12 | ING.1 | I pytest attuali passano sul nuovo storage |
| ING.3 | Adapter HTML (vendor A) + Store API generico | P1 | 12 | ING.2 | Run in dry-run equivalente a quello attuale |
| ING.4 | Port estrazione (step 5) e mapping JSON → MappingTable con FK | P1 | 10 | ING.1, CAT.2 | Stesso output dello step 5 su 50 prodotti campione |
| ING.5 | `TextEnricher` (Gemini) con policy, validazione schema, archivio versioni | P1 | 8 | ING.1 | Testi fuori policy finiscono in revisione |
| ING.6 | Immagini: pipeline hash → storage → rendition, protezione cover | P1 | 6 | CAT.4 | Foto cambiate rilevate per hash |
| ING.7 | `publish_drafts` via `catalog.services` con dry-run e pubblicazione programmata | P1 | 8 | CAT.10 | Bozza → prodotto EN/IT in un passo |
| ING.8 | `plan_stock_actions` / `plan_price_actions` + job e ReviewItem | P1 | 10 | ING.2 | Regole fornitore (404, stale, >15%, saldi) verdi |
| ING.9 | Adapter XLSX (vendor B), feed editore (vendor D), YAML light (vendor E), CSV generico | P1 | 16 | ING.4 | Un run dry-run per ciascun vendor |
| I18N.1 | Configurazione lingue, prefissi URL, middleware, selettore lingua | P1 | 4 | ARC.3 | `/it/` e `/de/` attivi su STAGE |
| I18N.2 | Base astratta `TranslationModel` con stato, `source_hash`, fallback | P1 | 6 | ARC.6 | Riusata da catalogo e vendor |
| I18N.3 | wagtail-localize per le pagine CMS | P1 | 4 | CMS.1 | Pagina tradotta con sync |
| I18N.5 | Cataloghi gettext per template ed email; diciture fiscali come chiavi | P1 | 6 | ORD.9 | Nessuna stringa hardcoded (lint) |
| I18N.6 | hreflang, canonical, sitemap per lingua con fallback escluso | P1 | 4 | SEO.1 | Validazione su campione di URL |
| CMS.1 | Wagtail: site, locale, page type base, navigazione e footer per lingua | P1 | 8 | ARC.3 | Home e pagine statiche EN/IT |
| CMS.7 | Tema frontend accessibile (WCAG 2.1 AA), componenti, design token | P1 | 24 | CMS.1 | Audit accessibilità superato |

### F2 — Checkout: fiscale, spedizioni, ordini, pagamenti (28 task, 258 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| ARC.10 | Seed dati sintetici: vendor con tutti i regimi, prodotti EN/IT, zone, ordini | P1 | 10 | CAT.1, TAX.1, SHP.1 | `make seed` riproducibile, usato dai test |
| TAX.1 | Modelli TaxRate, Territory, VendorTaxProfile storicizzato, VatValidation | P1 | 8 | ARC.7, VEN.1 | Migrazioni + admin |
| TAX.2 | Workflow di modifica regime (richiesta vendor → approvazione staff), storico | P1 | 6 | TAX.1 | Parità con revisione VTO |
| TAX.3 | `resolve_goods_tax` funzione pura + tabella casi T1–T18 come test parametrici | P1 | 12 | TAX.1 | 100% dei casi verdi, coverage 100% del modulo |
| TAX.4 | `resolve_shipping_tax` con ripartizione proporzionale | P1 | 6 | TAX.3 | T8–T13 verdi |
| TAX.5 | Client VIES con cache, timeout, fallimento chiuso | P1 | 6 | TAX.1 | Test con VIES simulato giù |
| TAX.7 | Territori speciali con CAP (dataset iniziale) | P1 | 4 | TAX.1 | T15 verde |
| SHP.1 | Modelli Carrier, Zone, VendorZoneRate, WeightBand (exclusion constraint), PickupOption, ConfigVersion | P1 | 10 | VEN.1 | Vincoli DB testati |
| SHP.2 | `match_postcode` + `resolve_zone` funzioni pure con casi 1–7 | P1 | 8 | SHP.1 | Casi verdi |
| SHP.3 | `quote_shipping` con esiti Rates/NeedsQuote/Blocked, policy oltre fascia, ritiro, soglia gratuita | P1 | 8 | SHP.2 | Casi 8–13 verdi |
| SHP.4 | Integrazione col carrello: package per vendor, IVA via motore fiscale | P1 | 6 | SHP.3, TAX.4, ORD.1 | Totali carrello corretti nei test misti |
| SHP.5 | Import della config LIVE v2 (JSON) nei nuovi modelli con report di parità | P1 | 6 | SHP.1 | Stesse tariffe su 200 combinazioni campione |
| SHP.9 | Preventivi: modelli, stati, `submit_vendor_quote`, `send_quote`, link con token, scadenza | P1 | 16 | ORD.2, SHP.3 | Flusso end-to-end con 2 vendor |
| SHP.10 | Preventivi: UI vendor e staff, email (cliente, vendor, staff), rate limit e honeypot | P1 | 10 | SHP.9 | Parità con 2.4.0 |
| ORD.1 | Cart, CartLine, sessione ospite/utente, merge al login | P1 | 8 | CAT.1 | Test merge carrello |
| ORD.2 | Order, VendorOrder, OrderLine con snapshot, numerazione, FSM | P1 | 12 | ORD.1 | Transizioni illegali impossibili (test) |
| ORD.3 | `build_checkout` (fiscale + spedizioni per vendor), anteprima e messaggi di blocco | P1 | 12 | TAX.4, SHP.4 | Carrello misto 3 vendor corretto |
| ORD.4 | `place_order` transazionale con StockReservation e verifica prezzi | P1 | 10 | ORD.2 | Test di concorrenza su stock 1 |
| ORD.5 | UI carrello e checkout (HTMX), campi B2B condizionali, VIES, Turnstile | P1 | 16 | ORD.3, TAX.5 | Checkout accessibile da tastiera |
| ORD.6 | Evasione vendor: accetta, spedisci con tracking, consegnato | P1 | 8 | ORD.2, CORE.9 | Vendor evade dal proprio pannello |
| ORD.7 | Area cliente: ordini, timeline, tracking, preventivi | P1 | 10 | ORD.2 | Parità con My Account |
| ORD.8 | Recesso online e flusso resi | P1 | 12 | PAY.4, CMP.6 | Pulsante di recesso conforme, ricevuta email |
| ORD.9 | Email transazionali per lingua (cliente, vendor, staff) | P1 | 10 | ORD.2 | Tutte le email in EN/IT/DE con test snapshot |
| ORD.10 | Kill-switch checkout e pagamenti, alert su pagamento senza transfer | P1 | 4 | ARC.8, PAY.3 | Kill-switch verificato su STAGE |
| PAY.1 | `PaymentGateway` + implementazione Stripe (PaymentIntent, transfer_group) | P1 | 12 | ORD.4 | Pagamento test end-to-end |
| PAY.2 | Webhook firmati e idempotenti, `on_payment_succeeded` | P1 | 8 | PAY.1, VEN.4 | Evento duplicato senza effetti |
| PAY.3 | Transfer per vendor, reverse transfer, riconciliazione con ledger | P1 | 10 | PAY.2 | 3 vendor in un ordine: 3 transfer corretti |
| PAY.4 | Rimborsi totali/parziali con IVA e commissioni stornate | P1 | 10 | PAY.3 | Casi di rimborso parziale verdi |

### F3 — Vendor, ledger, compliance (15 task, 138 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| VEN.1 | Modelli Vendor, traduzioni e policy, stati, slug | P1 | 8 | ARC.6 | CRUD admin + test stati |
| VEN.2 | VendorPlan e CommissionRule storicizzata con vincoli (no regola zero implicita) | P1 | 6 | VEN.1 | Test su validità temporali sovrapposte |
| VEN.3 | `compute_commission` funzione pura + suite casi reali (REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO) | P1 | 8 | VEN.2 | Tutti i casi del §4.4 verdi |
| VEN.4 | LedgerEntry append-only (vincolo DB/trigger), saldo, report per vendor | P1 | 10 | VEN.3 | UPDATE/DELETE impossibili; saldo = somma |
| VEN.5 | Flusso candidatura → onboarding → attivo, con contratto versionato e checklist DAC7 | P1 | 12 | VEN.1 | Vendor di prova attivato end-to-end |
| VEN.6 | Onboarding Stripe Express (account link, stato, webhook `account.updated`) | P1 | 8 | PAY.1 | Stato account sincronizzato |
| VEN.8 | Riconciliazione notturna ledger ↔ Stripe con report anomalie | P1 | 10 | VEN.4, PAY.3 | Differenza simulata → alert |
| VEN.9 | Dashboard vendor: saldo, movimenti, payout, documenti | P1 | 10 | CORE.9, VEN.4 | Vendor vede solo i propri movimenti |
| VEN.10 | Vetrina pubblica vendor per lingua (bio, policy, prodotti, categorie del negozio) | P1 | 8 | CAT.8 | Sidebar categorie corretta anche in IT (bug WCFM risolto per costruzione) |
| CMP.1 | Dataset DAC7 nel vendor + completezza + aggregati trimestrali + export | P1 | 12 | VEN.5, VEN.4 | Export di prova validato dal consulente |
| CMP.2 | Documento commissioni dal ledger + interfaccia `EInvoiceChannel` + export per vendor | P1 | 12 | VEN.4 | Documento di prova generato |
| CMP.3 | `GpsrInfo` con default per vendor, blocco pubblicazione, scheda prodotto, procedura di richiamo | P1 | 8 | CAT.1 | Prodotto senza GPSR non pubblicabile |
| CMP.4 | GDPR: export e anonimizzazione cliente, registro sub-processor, retention job | P1 | 10 | ORD.2 | Richiesta di cancellazione su ordine seed |
| CMP.6 | Recesso online, storico prezzi 30 gg (Omnibus), informative precontrattuali | P1 | 8 | ORD.2, CAT.1 | Prezzo barrato corretto nei test |
| CMP.7 | Audit accessibilità automatico in CI (axe) + revisione manuale + dichiarazione | P1 | 8 | CMS.7 | Zero violazioni critiche sulle pagine chiave |

### F4 — Contenuti, SEO, feed (8 task, 64 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| CMS.2 | Blocchi StreamField riusabili (hero, griglia prodotti, CTA, FAQ, chooser) | P1 | 10 | CMS.1, CAT.8 | Redattore compone la home senza codice |
| CMS.3 | Blog con schedulazione, autore, chooser prodotto/termine/vendor | P1 | 8 | CMS.2 | Articolo migrato pubblicato |
| CMS.4 | TermLandingPage e VendorLandingPage | P1 | 10 | CMS.2, CAT.8 | Pagina di un termine con i suoi prodotti |
| CMS.5 | LegalPage versionata + VendorRecruitmentPage con form candidatura | P1 | 6 | CMS.1, VEN.5 | Candidatura arriva in admin |
| SEO.1 | JSON-LD Product/Offer/Breadcrumb/ReturnPolicy/ShippingDetails, robots, canonical | P1 | 10 | CAT.7, SHP.3 | Rich Results Test senza errori |
| SEO.2 | Sitemap per lingua e tipo, `llms.txt` generato | P1 | 4 | I18N.6 | Sitemap valide |
| SEO.3 | Feed Google (EN/IT) con ID legacy, prezzi dal motore fiscale, spedizioni da tabelle | P1 | 8 | TAX.3, SHP.11 | Feed validato in Merchant Center STAGE |
| SEO.5 | Consenso cookie + Consent Mode v2 + eventi e-commerce | P1 | 8 | ORD.5 | Nessun tag prima del consenso |

### F5 — Migrazione, collaudo, go-live (11 task, 122 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| SEC.6 | Penetration test esterno e remediation | P1 | 16 | MIG.5 | Report senza criticità aperte |
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

### F6 — Dopo il go-live (P2) (26 task, 189 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| ARC.8 | Feature flag per ambiente e per vendor + kill-switch (checkout, import, pagamenti) | P2 | 6 | ARC.6 | Flag gestibili da admin |
| CORE.10 | Impersonazione per supporto con audit | P2 | 4 | CORE.8 | Ogni sessione impersonata in AuditLog |
| CAT.11 | Admin catalogo (Wagtail ViewSet): filtri, bulk, anteprima, stato traduzioni | P2 | 10 | CORE.8 | Staff modifica prodotti senza API |
| VEN.7 | Payout: eleggibilità, blocco se DAC7 incompleto, storico | P2 | 8 | VEN.4, PAY.3 | Test su ogni condizione di blocco |
| VEN.11 | Richiesta modifica regime fiscale da vendor con revisione staff ed email | P2 | 6 | TAX.2 | Parità con VTO 2.4 |
| TAX.6 | Import aliquote da fonte ufficiale + revisione staff + validità | P2 | 8 | TAX.1 | Aggiornamento simulato senza toccare ordini passati |
| TAX.8 | `distance_sales_eu` + `suggest_regime` dal ledger | P2 | 6 | VEN.4 | T16 verde |
| TAX.9 | Simulatore fiscale carrello in admin e API (`/tax/simulate`) | P2 | 6 | TAX.4, SHP.4 | Staff simula un carrello misto |
| TAX.10 | Report OSS trimestrale per paese e report per vendor | P2 | 8 | ORD.4 | Export CSV verificato su ordini seed |
| SHP.6 | Editor admin zone/tariffe con diff, impatto e versioni/rollback | P2 | 12 | SHP.1, CORE.8 | Rollback a versione precedente funzionante |
| SHP.7 | Simulatore carrello (admin + `/shipping/simulate`) | P2 | 6 | SHP.4 | Parità con simulatore attuale su casi seed |
| SHP.8 | Health check giornaliero + report | P2 | 6 | SHP.1, ARC.9 | Report via email con i 7 controlli |
| SHP.11 | Export tariffe per Google Merchant | P2 | 4 | SHP.1 | Feed senza tariffe hardcoded |
| ING.10 | Report giornaliero (email + pagina admin) con anomalie d'identità | P2 | 6 | ING.8 | Parità con `daily_report.py` |
| ING.11 | Import recensioni fornitore | P2 | 4 | ING.1, CMS.6 | Recensioni importate con data originale |
| ING.12 | API `/api/v1` di import per vendor esterni (upsert, stock, prezzi, media) | P2 | 8 | CORE.7, CAT.10 | Client di esempio in dry-run |
| ING.13 | Checklist e wizard admin "nuovo vendor da sorgente" | P2 | 8 | ING.9 | Vendor demo attivato senza codice |
| I18N.4 | Coda traduzioni in admin (per lingua, vendor, stato) + traduzione AI in bozza | P2 | 8 | I18N.2, ING.5 | Modifica EN → IT torna "da rivedere" |
| CMS.6 | Recensioni: modello, verifica acquisto, import fornitori, moderazione, risposta vendor | P2 | 10 | ORD.2 | Schema AggregateRating solo con recensioni valide |
| SEO.4 | Sync Merchant API + feed Meta | P2 | 8 | SEO.3 | Report delta giornaliero |
| SEO.6 | Cache pagine anonime, rendition responsive, budget CWV | P2 | 6 | CMS.7 | LCP < 2,5 s su pagina prodotto |
| AI.1 | Server MCP: auth, tool di lettura (catalogo, ordini, preventivi, simulazioni, report) | P2 | 12 | CORE.7 | Tool usabili da Claude Code con scope |
| AI.2 | Tool MCP mutativi con dry-run, conferma e audit | P2 | 10 | AI.1 | Ogni mutazione ha diff e audit |
| CMP.5 | DSA notice & action + termini vendor versionati con accettazione | P2 | 8 | VEN.5 | Segnalazione con esito tracciato |
| OPS.3 | Runbook: incidenti (con data breach 72 h), payout, aggiornamento aliquote, onboarding vendor | P2 | 8 | — | Runbook nel repo |
| OPS.4 | Aggiornamenti dipendenze automatici (PR) + SBOM | P2 | 3 | ARC.4 | PR di aggiornamento settimanali |

### F7 — Evoluzione (P3) (2 task, 18 h)

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---:|---|---|
| AI.3 | API pubblica di sola lettura del catalogo per agenti | P3 | 6 | CAT.9 | Ricerca per termine di tassonomia via API |
| AI.4 | Valutazione protocolli di agent commerce (ACP, WebMCP) e prototipo | P3 | 12 | PAY.1, AI.3 | ADR con raccomandazione |

