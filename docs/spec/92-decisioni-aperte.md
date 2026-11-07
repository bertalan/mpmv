# 92 — Decisioni aperte da approvare

Ogni decisione ha una **proposta** di default. Le voci ⚖️ richiedono il parere del commercialista o
del legale. Una volta presa, la decisione si registra come ADR nel repository e la riga si aggiorna.

| ID | Tema | Domanda | Proposta | Chi decide | Blocca |
|---|---|---|---|---|---|
| D-01 ⚖️ | IVA | Il paese di tassazione B2C è quello di **consegna** (regola UE delle vendite a distanza) o di fatturazione (come oggi)? | Consegna per il B2C; fatturazione solo per validare il B2B | Commercialista | TAX.3 |
| D-02 ⚖️ | IVA | Irlanda del Nord (`XI`): regole merci UE (Windsor Framework) o extra UE come oggi? | Regole merci UE per `BT*` | Commercialista | TAX.7 |
| D-03 ⚖️ | IVA | Il flag corriere `force_vat` (IVA del paese vendor sulla spedizione anche su export o esenti) ha una base normativa? | Eliminarlo | Commercialista | TAX.4 |
| D-04 | Business | I canoni dei piani a pagamento si fatturano dal giorno 1? | No: al lancio i piani sono solo regole di commissione e limiti; billing in fase 2 | Titolare | VEN.2 |
| D-05 | Business | Con rimborso totale la piattaforma restituisce commissione **e** costo d'incasso? E con rimborso parziale? | Totale: entrambi; parziale: proporzionali | Titolare | PAY.4 |
| D-06 | Spedizioni | Peso oltre l'ultima fascia: ultima fascia (oggi), preventivo o blocco? | Preventivo | Titolare | SHP.3 |
| D-07 | SEO | Le pagine tag `/tag-product/{slug}/` diventano `/{tassonomia}/{termine}/` con 301, o restano identiche? | Nuove URL parlanti con 301 (tutto il resto invariato) | Titolare + SEO | CAT.8, MIG.6 |
| D-08 ⚖️ | Pagamenti | Con *separate charges and transfers* i fondi transitano sul saldo Stripe della piattaforma: serve una verifica PSD2? | Verifica col consulente; transfer immediati alla conferma del pagamento | Legale | PAY.1 |
| D-09 | Pagamenti | Metodi oltre a carte, Apple Pay e Google Pay (SEPA, PayPal via Stripe, Klarna)? | Al lancio solo carte e wallet; altri dopo verifica della compatibilità con i transfer | Titolare | PAY.1 |
| D-10 | Privacy | Gestione del consenso: CMP esterna certificata o soluzione self-hosted? | CMP esterna certificata (meno manutenzione) | Titolare | SEO.5 |
| D-11 | Tecnica | Django 6.x con passaggio a 6.2 LTS prima del go-live, o 5.2 LTS da subito? | 6.x → 6.2 LTS | Tech lead | ARC.1 |
| D-12 | Tecnica | VPS gestita da noi o PaaS UE con Postgres gestito? | VPS + Postgres gestito (backup e aggiornamenti delegati) | Tech lead | ARC.5 |
| D-13 | Tecnica | Provider AI per l'enrichment: si resta su Gemini? | Sì, dietro `TextEnricher`; il provider è configurabile | Tech lead | ING.5 |
| D-14 | Prodotto | I vendor alimentati da pipeline possono modificare a mano i prodotti dalla dashboard? | Solo campi "locali" (testi rivisti, cover); prezzo e stock restano al fornitore | Titolare | CORE.9 |
| D-15 ⚖️ | Compliance | La piattaforma è microimpresa ai fini dell'EAA? | Progettare comunque WCAG 2.1 AA | Legale | CMP.7 |
| D-16 | Migrazione | Ordini storici: tutti o solo gli ultimi 24 mesi? Clienti senza ordini? | Tutti gli ordini (sola lettura); clienti senza ordini da 3 anni esclusi | Titolare + DPO | MIG.4 |
| D-17 | Business | Affiliazione, ambassador e referral di club e community: quando? | Dopo il go-live, come coupon o referral codes tracciati | Titolare | — |
| D-18 | Business | Il tedesco al go-live o subito dopo? | Struttura pronta al go-live, contenuti DE in fase 2 | Titolare | I18N.1 |
| D-19 | Operatività | Vendor E senza Stripe Connect: catalogo su richiesta finché non si collega? | Sì (`sales_mode=on_request`) | Titolare | VEN.6 |
| D-20 | Migrazione | Preventivi aperti al momento del cutover: chiusi prima o migrati? | Chiusi prima (freeze di 7 giorni sulle nuove richieste) | Titolare | MIG.9 |
