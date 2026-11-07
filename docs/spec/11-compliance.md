# 11 — Compliance normativa del marketplace

> Questo documento elenca **requisiti di prodotto** da implementare. Non è un parere legale: ogni
> voce marcata ⚖️ va confermata con consulente fiscale o legale (vedi [92-decisioni-aperte.md](92-decisioni-aperte.md)).

## 11.1 Stato attuale

| Obbligo | Oggi |
|---|---|
| DAC7 (reporting ricavi vendor) | Dati sparsi nelle meta WCFM; nessun export strutturato |
| GPSR (sicurezza prodotti, dal 13/12/2024) | **Nessun campo** |
| Fatturazione elettronica commissioni | Fuori dal sistema |
| GDPR | Complianz per i cookie; nessun flusso di export/cancellazione; dump LIVE copiati su STAGE |
| DSA (segnalazioni, trasparenza) | Nessun flusso dedicato |
| P2B (trasparenza verso i vendor) | Termini in pagine statiche |
| Omnibus (prezzo più basso 30 giorni, recensioni) | Non gestito (nessuna promozione strutturata) |
| European Accessibility Act (dal 28/06/2025) | Non valutato |
| Funzione di recesso online (dal 19/06/2026) | **Assente** |

## 11.2 Requisiti

### CMP.1 — DAC7 `[P1]`
- Dataset raccolto all'onboarding del vendor: denominazione, P.IVA/codice fiscale, indirizzo della sede,
  paese di residenza fiscale, IBAN, stabile organizzazione. Completezza = condizione per i payout.
- Aggregazione per vendor e trimestre dal ledger (corrispettivi, commissioni, numero di operazioni).
- Export annuale nel formato dell'autorità del **paese di stabilimento della piattaforma**
  (`PlatformOperator`). ⚖️ Verificare le soglie di esclusione (es. vendor con meno di 30 vendite e meno di 2.000 €).

### CMP.2 — Fatturazione delle commissioni `[P1]`
- Documento commissioni piattaforma → vendor per periodo, generato dal ledger.
- Canale dipendente dal `PlatformOperator`: per una piattaforma italiana è SDI (FatturaPA), con le
  diciture del regime fiscale della piattaforma ⚖️; interfaccia astratta `EInvoiceChannel` (SDI tramite intermediario
  oggi, altri canali in futuro, predisposizione ViDA).
- Le fatture verso i clienti restano del vendor: la piattaforma fornisce i dati per riga e per ordine
  (export per vendor). ⚖️ Chiarire il confine nel contratto vendor.

### CMP.3 — GPSR `[P1]`
```python
class GpsrInfo(Model):
    manufacturer_name; manufacturer_address; manufacturer_contact
    eu_responsible_name; eu_responsible_address; eu_responsible_contact  # obbligatorio se produttore extra UE
    product_identifiers (lotto, modello, GTIN)
    warnings (tradotte); safety_documents (file)
```
- Visibile nella scheda prodotto; **pubblicazione bloccata** se mancano i campi obbligatori; il
  contratto di import li richiede. Default per vendor (il produttore spesso coincide col vendor).
- Procedura di richiamo o rimozione rapida su segnalazione delle autorità o di sicurezza.

### CMP.4 — GDPR operativo `[P1]`
- Export dei dati del cliente (JSON/PDF) e cancellazione come **anonimizzazione** (gli ordini restano
  per obblighi contabili).
- Registro dei trattamenti e sub-processor (Stripe, corrieri, provider email, provider AI, hosting),
  retention per tipo di dato, DPA.
- Ambienti non di produzione solo con dati sintetici o anonimizzati (oggi si clona LIVE su STAGE).
- Consenso cookie con Consent Mode (vedi [10](10-contenuti-seo-ai.md)).

### CMP.5 — DSA e P2B `[P2]`
- Modulo di segnalazione di contenuti illeciti (notice & action) con tracciamento, esito motivato e
  comunicazione al vendor.
- Tracciabilità dei vendor (KYBC): dati identificativi verificati prima dell'attivazione (copre anche D.1).
- Termini piattaforma-vendor versionati con accettazione tracciata; preavviso per modifiche e
  sospensioni; trasparenza sui criteri di ordinamento dei risultati (pagina dedicata).

### CMP.6 — Diritti del consumatore `[P1]`
- **Funzione di recesso online** (direttiva UE 2023/2673, che modifica la direttiva 2011/83/UE):
  pulsante ben visibile per l'intera durata del recesso, dati minimi, conferma, ricevuta su supporto
  durevole. ⚖️ Verificare il recepimento italiano e i dettagli dell'etichetta del pulsante.
- Informazioni precontrattuali per lingua; garanzia legale; recesso di 14 giorni per paese del consumatore.
- Geo-blocking: nessuna discriminazione per residenza nei paesi serviti.
- Omnibus: prezzo barrato = prezzo più basso degli ultimi 30 giorni (storico prezzi in `catalog`);
  informativa sulla verifica delle recensioni.

### CMP.7 — European Accessibility Act `[P1]`
- I servizi di e-commerce rientrano nell'EAA. ⚖️ Le **microimprese** di servizi (meno di 10 dipendenti
  e fatturato o bilancio ≤ 2 M€) sono esentate: verificare lo status della piattaforma.
- Raccomandazione: progettare comunque a livello **WCAG 2.1 AA** (EN 301 549). Costa poco se fatto
  dall'inizio, è un requisito di fatto per crescere, e i vendor con più di 10 dipendenti potrebbero
  chiederlo. Dichiarazione di accessibilità pubblicata.

### CMP.8 — Contenuti generati con AI `[P3]`
- Le descrizioni prodotto sono generate con AI e riviste. ⚖️ Valutare eventuali obblighi di trasparenza
  dell'AI Act per i contenuti pubblicati; in ogni caso tracciare in `ProductTranslation` l'origine
  (`machine` / `reviewed`).

## 11.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| CMP.1 | Dataset DAC7 nel vendor + completezza + aggregati trimestrali + export | P1 | 12 | VEN.5, VEN.4 | Export di prova validato dal consulente |
| CMP.2 | Documento commissioni dal ledger + interfaccia `EInvoiceChannel` + export per vendor | P1 | 12 | VEN.4 | Documento di prova generato |
| CMP.3 | `GpsrInfo` con default per vendor, blocco pubblicazione, scheda prodotto, procedura di richiamo | P1 | 8 | CAT.1 | Prodotto senza GPSR non pubblicabile |
| CMP.4 | GDPR: export e anonimizzazione cliente, registro sub-processor, retention job | P1 | 10 | ORD.2 | Richiesta di cancellazione su ordine seed |
| CMP.5 | DSA notice & action + termini vendor versionati con accettazione | P2 | 8 | VEN.5 | Segnalazione con esito tracciato |
| CMP.6 | Recesso online, storico prezzi 30 gg (Omnibus), informative precontrattuali | P1 | 8 | ORD.2, CAT.1 | Prezzo barrato corretto nei test |
| CMP.7 | Audit accessibilità automatico in CI (axe) + revisione manuale + dichiarazione | P1 | 8 | CMS.7 | Zero violazioni critiche sulle pagine chiave |
