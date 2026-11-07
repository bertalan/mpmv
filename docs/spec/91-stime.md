# 91 — Stime e piano di rilascio

## 91.1 Come leggere le ore

- Le ore dei micro-task (vedi [90-backlog.md](90-backlog.md)) comprendono **sviluppo e test
  automatici**. Assumono uno sviluppatore senior che conosce Django e lavora con un assistente AI
  agentico (Claude Code o simili).
- Non comprendono i costi trasversali, che si aggiungono qui sotto.
- Sono stime top-down per micro-task, non un preventivo: vanno riviste dopo la fase F0, quando
  esiste lo scheletro del progetto e si misura la velocità reale.

## 91.2 Totale per il go-live (solo P1)

| Voce | Ore | Come è calcolata |
|---|---:|---|
| Micro-task P1 | 1.023 | Somma dal backlog generato |
| Collaudo integrato ed end-to-end, test esplorativi | 100 | ~10% dei P1 |
| Revisione umana obbligatoria su fiscale, pagamenti, ledger, ordini, sicurezza | 50 | ~15% delle ore di TAX, PAY, VEN, ORD, SEC |
| Coordinamento, ADR, incontri con commercialista e legale | 80 | ~8% dei P1 |
| **Subtotale** | **1.253** | |
| Riserva per imprevisti (10–25% dei P1) | 100–255 | Dominio fiscale e pagamenti, dati legacy sporchi |
| **Totale go-live** | **≈ 1.350–1.510** | |

Dopo il go-live: P2 = 189 h e P3 = 18 h (MCP, editor zone avanzato, report OSS, coda traduzioni,
recensioni, payout avanzati…), circa **210–250 h** con la riserva.

## 91.3 Confronto con il piano di agosto

| | Piano di agosto | Questa revisione |
|---|---:|---:|
| Perimetro | Parità completa WCFM + moduli commerciali P1 | Parità **di ciò che si usa davvero** + compliance 2026 |
| Ore totali (sviluppo, test, migrazione) | 1.740–2.540 | 1.350–1.510 al go-live |
| Con sviluppo AI-assistito | 1.450–2.100 | già incluso |

Da dove viene la differenza:

- **Componenti in meno**: niente Celery/Redis/Elasticsearch e niente dashboard vendor completa.
- **Moduli non migrati**: membership con billing, affiliate, staff, analytics.
- **Riuso delle pipeline** come codice Python nello stesso progetto: niente client HTTP verso PHP,
  niente promozione STAGE → LIVE.
- **Sviluppo agentico** usato in modo sistematico sulle parti meccaniche (modelli, CRUD, admin, test
  parametrici). Le parti fiscali e di pagamento restano a revisione umana riga per riga.

Aggiunte rispetto ad agosto: recesso online, EAA/accessibilità, GPSR operativo, server MCP.

## 91.4 Calendario indicativo

Il percorso critico P1 è breve (~126 h: fondamenta → catalogo → ordini → pagamenti → rimborsi):
il calendario dipende dalla capacità del team più che dalle dipendenze.

| Team | Mesi al go-live | Note |
|---|---|---|
| 1 senior a tempo pieno (~150 h utili al mese) | 9–10 | Sequenza F0 → F5 |
| 2 senior (uno su F2/F3, uno su F1/F4/F5) | 5–6 | Coordinamento incluso nelle ore |
| 1 senior + redattore per contenuti e traduzioni | 8–9 | Il redattore prende MIG.7, revisione testi, CMS |

### Fasi e milestone

| Fase | Contenuto | Milestone verificabile |
|---|---|---|
| F0 Fondamenta | Repo, CI, deploy, core, identità, sicurezza di base | STAGE online con login MFA e admin |
| F1 Catalogo e import | Catalogo, multilingua, pipeline del vendor A e degli altri vendor, tema | Catalogo LIVE-equivalente navigabile su STAGE, alimentato dalle pipeline |
| F2 Checkout | Fiscale, spedizioni, preventivi, carrello, ordini, Stripe | Ordine multi-vendor pagato in test mode con transfer corretti |
| F3 Vendor e compliance | Ledger, payout, dashboard vendor, DAC7, GPSR, GDPR, recesso | Vendor pilota opera da solo su STAGE |
| F4 Contenuti e SEO | Pagine Wagtail, blog, landing di tassonomia, JSON-LD, feed | Rich Results e feed Merchant validati |
| F5 Go-live | Migrazione, parità, prove generali, pentest, cutover | Terza prova generale senza blocchi → go-live |

Una milestone si chiude solo con la demo funzionante su STAGE e i test di regressione del dominio verdi.

## 91.5 Costi esterni da preventivare a parte

- Penetration test esterno (SEC.6, le ore indicate sono per la remediation).
- Commercialista e legale per le decisioni ⚖️ in [92-decisioni-aperte.md](92-decisioni-aperte.md).
- Intermediario SDI per le fatture commissioni; CMP per i cookie; provider email transazionale.
- Hosting: VPS UE, Postgres gestito, object storage e CDN, error tracking.
- Grafica e UX custom, se si vuole andare oltre un tema pulito e accessibile.

In cambio spariscono i costi ricorrenti delle licenze premium WordPress (WCFM Ultimate, Membership,
Affiliate, Groups & Staffs, Analytics, WPML, WPSSO Premium e add-on) e, soprattutto, le ore di
manutenzione per aggiornamenti, incompatibilità e fix (STAGE, matrici di test, patch delle opzioni).
