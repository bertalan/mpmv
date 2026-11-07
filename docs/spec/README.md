# piac — Ricostruzione in Python di un marketplace multivendor

Specifica funzionale e tecnica per sostituire lo stack WordPress + WooCommerce + WCFM + WPML
con un sistema **Django + Wagtail** più semplice da gestire.

Il documento è stato ricavato dall'analisi del codice attuale: plugin custom IVA e spedizioni,
mu-plugin, 132 route API, pipeline Python di import, report di audit e configurazioni LIVE.
Rivede anche il piano di ricostruzione di agosto 2026.

## Come leggere

> **Versione pubblica.** I vendor sono pseudonimizzati (A–E), gli ordini di riferimento sono
> indicati con il nome del caso (es. FEE-NETTO) e i dettagli di sicurezza del sistema attuale sono
> omessi. Gli identificativi del sistema attuale (plugin, classi, meta) usano il prefisso
> generico `mp`. La versione integrale e la mappa degli pseudonimi sono in `private/`, fuori da git.

- Ogni documento di dominio ha la stessa struttura:
  1. **Come funziona oggi**: classi, funzioni e dati del sistema WordPress, con i bug noti.
  2. **Come funzionerà**: modelli (classi Django), servizi e funzioni pure con le firme, algoritmi, regole.
  3. **Casi di regressione**: bug storici che diventano test obbligatori (dove rilevante).
  4. **Task**: micro-task con ID, priorità, ore, dipendenze e criterio di "fatto".
- Macro-task = prefisso dell'ID (es. `TAX` = motore fiscale). Micro-task = riga (es. `TAX.3`).
- Priorità: **P1** necessario al go-live · **P2** subito dopo · **P3** evoluzione.
- Il backlog completo ([90-backlog.md](90-backlog.md) e `backlog.csv`) è **generato** dalle tabelle dei
  documenti: `make docs` (oppure `uv run tools/build_docs.py`). Lo script verifica anche ID unici,
  dipendenze esistenti e assenza di cicli. Lo stesso comando rigenera `build/piac-specifica.html`
  (fuori dal versionamento); `make docs-check` segnala backlog non aggiornato.

## Indice

| # | Documento | Contenuto |
|---|---|---|
| 00 | [Visione e revisione](00-visione-e-revisione.md) | Idea, principi, delta rispetto al piano di agosto |
| 01 | [Stack e architettura](01-stack-e-architettura.md) | Wagtail e alternative a confronto, monolite modulare, convenzioni |
| 02 | [Identità, permessi, API, backoffice](02-core-identita-permessi.md) | Ruoli, isolamento vendor, API v1 che sostituisce `api.php` |
| 03 | [Catalogo](03-catalogo.md) | Prodotti, varianti, tassonomie di navigazione, slug SKU, redirect, ricerca |
| 04 | [Vendor, commissioni, ledger](04-vendor-commissioni-ledger.md) | Piani, regole di commissione, ledger, payout |
| 05 | [Motore fiscale](05-fiscale.md) | Regimi IVA, albero decisionale, territori speciali, test |
| 06 | [Spedizioni e preventivi](06-spedizioni-preventivi.md) | Zone, fasce di peso, CAP, flusso preventivo |
| 07 | [Carrello, ordini, pagamenti](07-carrello-ordini-pagamenti.md) | Sotto-ordini, Stripe Connect, rimborsi, recesso |
| 08 | [Import e pipeline](08-import-pipeline.md) | Riuso delle pipeline Python, adapter per vendor, job |
| 09 | [Multilingua](09-multilingua.md) | Traduzioni dei modelli, wagtail-localize, workflow |
| 10 | [Contenuti, SEO, canali AI](10-contenuti-seo-ai.md) | Pagine Wagtail, JSON-LD, feed, MCP |
| 11 | [Compliance](11-compliance.md) | DAC7, GPSR, GDPR, DSA, recesso online, EAA |
| 12 | [Sicurezza, infrastruttura, operatività](12-sicurezza-infra-operativita.md) | Azioni immediate, requisiti, deploy, backup |
| 13 | [Migrazione e cutover](13-migrazione-cutover.md) | ETL, parità, runbook di go-live e rollback |
| 90 | [Backlog](90-backlog.md) | Tutti i micro-task per fase (generato) |
| 91 | [Stime e piano](91-stime.md) | Ore, calendario, fasi, costi esterni |
| 92 | [Decisioni aperte](92-decisioni-aperte.md) | 20 decisioni da approvare, con proposta |

## Sintesi

- **Stack raccomandato**: Django (6.x → 6.2 LTS) + commerce custom e snello + Wagtail per contenuti e
  backoffice, PostgreSQL come unico datastore (anche per code e ricerca), HTMX, Stripe Connect.
- **Semplificazioni principali**: una sola entità per prodotto (non due post EN/IT), una sola fonte per
  le spedizioni, ledger e commissioni nostri, pipeline di import nello stesso progetto, nessuno script
  operativo fuori dal versionamento.
- **Perimetro go-live**: 113 micro-task P1, **≈ 1.350–1.510 h** inclusi collaudo, revisione e riserva.
  Il piano di agosto stimava 1.740–2.540 h.
- **Da fare subito**, indipendentemente dalla ricostruzione: ruotare le credenziali trovate in chiaro
  (vedi [12](12-sicurezza-infra-operativita.md) §12.1).

## Checklist di approvazione

- [ ] 00 Visione e principi
- [ ] 01 Stack (decisioni D-11, D-12)
- [ ] 02–13 Documenti di dominio (una spunta per documento)
- [ ] 92 Decisioni aperte: proposte accettate o modificate
- [ ] 91 Perimetro P1 e calendario
- [ ] Import del `backlog.csv` nello strumento di gestione dei task
