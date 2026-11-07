# 00 — Visione, perimetro e revisione del piano di agosto

## 0.1 L'idea in una pagina

Il sistema attuale è un marketplace multivendor verticale e curato. I clienti navigano anche
**per tassonomie di navigazione** (oggi una tassonomia a due livelli, tipo marca → modello). Il sito è
bilingue EN/IT, con il tedesco già previsto.

Modello operativo attuale (da mantenere):

| Aspetto | Come funziona |
|---|---|
| Ruolo della piattaforma | Facilitatore: il **vendor fattura e spedisce**, la piattaforma trattiene commissione e costo d'incasso |
| Incasso | Stripe Connect (account Express dei vendor), split automatico dei fondi |
| Vendor attivi | 5 profili, qui pseudonimizzati A–E: A (catalogo ampio, pipeline dal sito del fornitore), B (listino XLSX), D (regime esente), E (catalogo su preventivo), C (0 prodotti) |
| Catalogo | Migliaia di prodotti per lingua, in gran parte importati da **pipeline Python** (scraping, estrazione, AI Gemini, immagini) |
| Fiscalità | Regimi per vendor: OSS, sotto soglia (`domestic_only`), esente (art. 74 / forfettario); reverse charge B2B; export extra UE |
| Spedizioni | Zone per corriere × vendor × fascia di peso (corriere principale, corriere proprio di un vendor, aggregatore, ritiro in sede) + **preventivo** per i paesi non coperti |
| Mercati | UE, UK e resto del mondo |
| Crescita attesa | Da pochi vendor a decine, con migliaia di prodotti |

Valore per il vendor (dalla presentazione commerciale): import del catalogo assistito, traduzioni, SEO per pagine
di tassonomia, articoli editoriali e vendor in evidenza, cross-selling tra partner. Tutto questo oggi richiede
lavoro manuale su WordPress: **il nuovo sistema deve rendere economico aggiungere il vendor
numero 6, 10, 25.**

## 0.2 Perché ricostruire

Problemi strutturali emersi dagli audit (dettaglio nei documenti di dominio):

1. **Il core di business è già custom.** IVA (`mp-vendor-tax-override`), spedizioni e preventivi
   (`mp-multicarrier-shipping`), slug SKU e redirect (`mp-sku-permalink`), ~20 mu-plugin.
   WooCommerce e WCFM fanno da "telaio", ma bisogna continuamente aggirarli.
2. **Bug strutturali di WCFM sulle commissioni**: flag `tax_enable` letto con `isset()`; transaction
   charge calcolata sul netto; percentuali congelate errate (casi REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO in [04](04-vendor-commissioni-ledger.md)). Il payout
   dipende da un ledger che non controlliamo.
3. **Doppia rappresentazione dei dati**: zone del plugin custom e zone WCFM da sincronizzare; prodotti EN e IT
   come post separati WPML, con molti prodotti IT finiti su categorie EN; cache delle tassonomie
   per negozio popolata solo in EN.
4. **Operatività pesante**: 132 route PHP (~35% fix o diagnostica una tantum), decine di script
   `fix_*` / `diagnose_*` caricati sui server, clonazione STAGE→LIVE cross-DB, aggiornamenti premium
   WPML bloccati, WCFM testato solo fino a WP 7.0.7.
5. **Lacune di compliance**: nessun campo GPSR; IVA editoria indistinguibile da export e reverse
   charge (stessa classe `zero-rate`); territori speciali UE non gestiti.

## 0.3 Principi del nuovo sistema ("meno oneroso nella gestione")

| # | Principio | Conseguenza pratica |
|---|---|---|
| P1 | **Una sola fonte di verità** per ogni dato | Prodotto unico con traduzioni (non due post); zone di spedizione in un solo posto; ledger nostro |
| P2 | **Logica di business in funzioni pure e testate** | `resolve_tax()`, `quote_shipping()`, `compute_commission()` sono funzioni senza I/O, coperte dai casi di regressione degli audit |
| P3 | **Pochi componenti in esercizio** | Django + PostgreSQL + object storage. Niente Redis, Celery o Elasticsearch finché non servono: code e ricerca stanno su Postgres |
| P4 | **Ogni operazione è un comando versionato con dry-run** | Gli script `fix_*.php` diventano management command o tool MCP con anteprima del diff e audit |
| P5 | **Configurazione, non costanti** | Aliquote, paese della piattaforma, soglie, piani, fasce: tutto a DB con validità temporale |
| P6 | **Snapshot immutabili sugli ordini** | Prezzi, aliquote, spedizione e commissione congelati al momento dell'ordine; ricalcoli solo espliciti e tracciati |
| P7 | **Prima la parità, poi le novità** | Cutover solo a parità funzionale P1; promozioni, recensioni, affiliazione e simili vengono dopo |
| P8 | **Aggiungere un vendor = dati, non codice** | Onboarding via form + mapping + adapter di import standard (YAML / CSV / Store API / scraping) |
| P9 | **Indipendente dal verticale** | Nessun concetto di settore nel codice: tassonomie, attributi, pagine di navigazione e regole di catalogo sono configurazione. Lo stesso sistema deve poter servire mercati diversi (es. fiori, articoli per il cucito) |

## 0.4 Revisione del piano di agosto (17/08/2026)

Il piano di agosto resta valido come **mappa dei rischi** (fiscale, split payment, compliance,
sicurezza). Va però rivisto a fondo su sette punti.

| # | Piano di agosto | Revisione (novembre 2026) | Motivo |
|---|---|---|---|
| R1 | Solo perimetro, "nessun dettaglio implementativo" | Questo documento definisce **modelli, servizi, firme di funzione e algoritmi** | Serve per derivare task stimabili e verificabili |
| R2 | Wagtail come centro del sistema, commerce "da decidere" | **Django è il centro**, con commerce custom e snello; Wagtail solo per contenuti e backoffice staff (vedi [01](01-stack-e-architettura.md)) | Il dominio difficile (IVA, spedizioni, preventivi, split) è comunque custom; framework commerce generici aggiungono peso senza togliere lavoro |
| R3 | Celery/Redis, Sentry, coda dedicata | **Code e cron su PostgreSQL** (task framework di Django + worker DB), ricerca Postgres full-text e trigram | Principio P3: un servizio in meno da gestire |
| R4 | Dashboard vendor completa (sostituto WCFM) | **Dashboard minima**: ordini, preventivi, payout, richieste di modifica fiscale. Il catalogo arriva soprattutto dalle pipeline | Oggi i vendor usano solo dashboard, prodotti, ordini e pannello preventivo; l'onboarding è manuale |
| R5 | Membership, affiliate, staff come moduli | Piani = **regole di commissione e limiti** (nessun billing di abbonamento al lancio); affiliate e staff rimandati | Affiliate e Groups&Staffs non risultano in uso; i piani pubblicizzati non governano i vendor reali (piano 2241 "Special Free") |
| R6 | Mancano EAA, recesso online, commercio via agenti | Aggiunti: **European Accessibility Act**, **funzione di recesso online** (dir. UE 2023/2673), **GPSR come P1**, canale **MCP / agent commerce** (vedi [11](11-compliance.md), [10](10-contenuti-seo-ai.md)) | Novità normative e di mercato 2025–2026 |
| R7 | Stima 1.740–2.540 h, AI −15–20% | **Stima rivista** con perimetro ridotto e sviluppo agentico: vedi [91-stime.md](91-stime.md) | Perimetro più snello (R3–R5) e flusso di lavoro AI maturo; le parti fiscali e di pagamento restano a revisione umana |

Altre correzioni di fatto rispetto al piano di agosto:

- I vendor sono **5 profili**, non 4: il vendor C esiste, ma senza prodotti.
- Lo split Stripe **non funziona per tutti**. Gli ordini pagati con `stripe_cc` non vengono divisi
  (crediti vendor orfani), e il vendor E non è collegato a Stripe Connect.
- Il `g:id` del feed Google è l'**ID post WordPress**: va conservato (`legacy_wp_id`), altrimenti si
  perde lo storico in Merchant Center.
- Sono emerse alcune **azioni di sicurezza immediate** sul sistema attuale, indipendenti dalla
  ricostruzione (elenco riservato, vedi [12](12-sicurezza-infra-operativita.md) §12.1).

## 0.5 Fuori perimetro (dichiarato)

- Grafica e UX custom oltre un tema pulito e accessibile (stimabile a parte).
- Consulenza fiscale e legale: le domande aperte sono raccolte in [92-decisioni-aperte.md](92-decisioni-aperte.md).
- Rielaborazione fiscale retroattiva degli ordini storici: migrati in sola lettura.
- Funzioni WCFM non in uso: Analytics, Groups&Staffs, Delivery, Product Hub, Elementor.
