# 04 — Vendor, piani, commissioni, ledger e payout

## 4.1 Come funziona oggi

**Anagrafica** (user meta WCFM): `wcfmmp_profile_settings` (nome negozio, descrizione EN,
indirizzo, blocco commissione), `_store_description_{lang}`, `wcfm_policy_vendor_options_{en|it}`
(spedizione, rimborso, annullamento), `wcfm_store_slug`, logo; meta Stripe (`stripe_user_id`,
`vendor_connected`, `stripe_payouts_enabled`); meta fiscali VTO (vedi [05](05-fiscale.md)).

**Piani** (WCFM Membership, pagamento `by_wcfm`):

| Piano | Canone | Commissione | Prodotti |
|---|---|---|---|
| Base | gratuito | più alta | limite basso |
| Intermedio | canone mensile, con prova | media | limite medio |
| Avanzato | canone mensile più alto | più bassa | illimitati |
| Speciale | — | da profilo | — |

I vendor reali sono sul piano speciale con la commissione definita nel profilo di ciascuno. Sono
previsti anche promozioni di lancio a tempo (commissione ridotta per i primi vendor).

**Commissione e costo d'incasso** (modello dichiarato):
`admin = imponibile × % + (lordo × 4% + 2 €)` e `vendor = lordo − admin`.
Nel regime fiscale attuale della piattaforma la commissione è **senza IVA**.

**Payout**: `stripe_split` in `destination_charges`; WCFM crea il prelievo `by_split_pay` già
`completed` al charge. Prelievo standard (non split) su `wc-completed`, mensile, minimo 50 €,
attesa 7 giorni. Quattro vendor su cinque collegati a Stripe Express (il vendor E no).

**Bug storici** (diventano test, vedi §4.4): `tax_enable` letto con `isset()`; transaction charge
sul netto invece che sul lordo (caso FEE-NETTO: 9,3424 attesi, 8,07 calcolati); IVA calcolata su
commissione + fee (34,07 invece di 26,00); regola congelata a `fixed 0` (caso REGOLA-ZERO) o 25% (caso PERC-CONGELATA);
ordini `stripe_cc` senza split con crediti vendor orfani (caso NO-SPLIT).

## 4.2 Come funzionerà

### Modelli

```python
class Vendor(Model):
    legal_name; display_name; slug (UNIQUE)
    status = CharField(choices=["applicant", "onboarding", "active", "suspended", "closed"])
    country; vat_number; tax_code; registered_address (JSON strutturato)
    contact_email; phone; logo (Image)
    plan = FK(VendorPlan)
    sales_mode_default = CharField(choices=["buy", "on_request"])
    enquiries_enabled: bool                    # ex plugin "catalog status" del vendor E
    payout_account = FK(PayoutAccount, null=True)
    dac7_complete: bool (derivato)             # blocca i payout se False
    legacy_wp_user_id

class VendorTranslation(Model): vendor; language; tagline; description; shipping_policy; refund_policy; cancellation_policy

class VendorPlan(Model):                       # i piani sono DATI, non codice
    code ("base", "intermedio", "avanzato", "speciale", "lancio"); name (tradotto)
    max_products (null = illimitati); features (JSON: in_evidenza, api_sync, custom_tax…)
    monthly_fee (MoneyField, null)             # fatturazione canone: fase 2 (D-04)

class CommissionRule(Model):                   # storicizzata: mai modificata, solo nuove righe
    vendor (null = default piattaforma); plan (null)
    percent = DecimalField(5,2)                # 20.00
    base = CharField(choices=["net_goods", "net_goods_and_shipping"])   # oggi: solo merce netta
    fee_percent = DecimalField(5,2)            # 4.00 — costo d'incasso
    fee_fixed = MoneyField()                   # 2.00
    fee_base = CharField(choices=["gross_paid"])   # sempre il lordo pagato (fix caso FEE-NETTO)
    valid_from; valid_to; promo_label ("promo di lancio")
    # vincolo: percent > 0 o promo esplicita; nessuna regola "fixed 0" implicita (fix caso REGOLA-ZERO)

class PayoutAccount(Model):
    vendor; provider ("stripe"); external_id (acct_…); status; payouts_enabled; platform_entity

class LedgerEntry(Model):                      # append-only, mai UPDATE/DELETE
    vendor; vendor_order = FK(VendorOrder, null=True)
    kind = CharField(choices=["sale", "shipping", "commission", "payment_fee", "refund",
                              "commission_reversal", "adjustment", "payout", "transfer"])
    amount = MoneyField()                      # segno: + credito vendor, − debito vendor
    platform_entity = FK(PlatformOperator)     # multi-entità (es. cambio di sede in altro paese UE)
    gateway_account; external_ref (tr_…, po_…, re_…)
    created_at; created_by; memo

class Payout(Model): vendor; amount; status; period; external_ref; ledger_entries M2M
```

### Funzioni pure

```python
@dataclass(frozen=True)
class CommissionInput:
    goods_net: Decimal        # imponibile merce del vendor nell'ordine
    shipping_net: Decimal
    gross_paid: Decimal       # quota del vendor sul totale pagato (IVA inclusa)
    rule: CommissionRuleSnapshot

def compute_commission(i: CommissionInput) -> CommissionResult:
    """commission = round(base × percent)          # base = goods_net (default)
       payment_fee = round(gross_paid × fee_percent + fee_fixed)
       vendor_net = gross_paid − commission − payment_fee
       IVA sulla commissione secondo PlatformOperator.commission_vat_mode (oggi "none")."""

def vendor_balance(vendor, until: datetime) -> Decimal      # somma ledger
def payout_eligibility(vendor) -> Eligibility               # DAC7 completo, account abilitato, minimo, attesa
```

### Flussi

1. **Candidatura vendor**: form pubblico (Turnstile) → `applicant` → revisione staff → `onboarding`
   (dati DAC7, account Stripe Express via link di onboarding, accettazione contratto versionato,
   regime fiscale) → `active`. Ogni passaggio è in `AuditLog`.
2. **Ordine pagato**: per ogni `VendorOrder` si congela lo snapshot della regola, si calcola
   `compute_commission`, si scrivono le righe di ledger (`sale`, `shipping`, `commission`, `payment_fee`).
3. **Trasferimento**: con *separate charges and transfers* (vedi [07](07-carrello-ordini-pagamenti.md)) si crea un
   transfer Stripe per vendor di importo `vendor_net`, registrato come `transfer`.
4. **Rimborso**: righe `refund` e `commission_reversal` proporzionali; regola commerciale attuale
   "con rimborso totale la piattaforma restituisce commissione e costo d'incasso" (da confermare, D-05).
5. **Riconciliazione notturna**: ledger ↔ transfer e balance Stripe; differenze → alert e report.

### Dashboard vendor (minimo indispensabile)

Ordini e righe propri, inserimento preventivi spedizione, prodotti (modifica di testi, prezzo e stock
se il vendor non è alimentato da pipeline), saldo e payout, documenti (contratto, fatture commissione),
richiesta di modifica del regime fiscale (approvata dallo staff, come oggi), policy del negozio per lingua.

## 4.3 Task

| ID | Micro-task | Pri | Ore | Dipende da | Done quando |
|---|---|---|---|---|---|
| VEN.1 | Modelli Vendor, traduzioni e policy, stati, slug | P1 | 8 | ARC.6 | CRUD admin + test stati |
| VEN.2 | VendorPlan e CommissionRule storicizzata con vincoli (no regola zero implicita) | P1 | 6 | VEN.1 | Test su validità temporali sovrapposte |
| VEN.3 | `compute_commission` funzione pura + suite casi reali (REGOLA-ZERO, PERC-CONGELATA, FEE-NETTO) | P1 | 8 | VEN.2 | Tutti i casi del §4.4 verdi |
| VEN.4 | LedgerEntry append-only (vincolo DB/trigger), saldo, report per vendor | P1 | 10 | VEN.3 | UPDATE/DELETE impossibili; saldo = somma |
| VEN.5 | Flusso candidatura → onboarding → attivo, con contratto versionato e checklist DAC7 | P1 | 12 | VEN.1 | Vendor di prova attivato end-to-end |
| VEN.6 | Onboarding Stripe Express (account link, stato, webhook `account.updated`) | P1 | 8 | PAY.1 | Stato account sincronizzato |
| VEN.7 | Payout: eleggibilità, blocco se DAC7 incompleto, storico | P2 | 8 | VEN.4, PAY.3 | Test su ogni condizione di blocco |
| VEN.8 | Riconciliazione notturna ledger ↔ Stripe con report anomalie | P1 | 10 | VEN.4, PAY.3 | Differenza simulata → alert |
| VEN.9 | Dashboard vendor: saldo, movimenti, payout, documenti | P1 | 10 | CORE.9, VEN.4 | Vendor vede solo i propri movimenti |
| VEN.10 | Vetrina pubblica vendor per lingua (bio, policy, prodotti, categorie del negozio) | P1 | 8 | CAT.8 | Sidebar categorie corretta anche in IT (bug WCFM risolto per costruzione) |
| VEN.11 | Richiesta modifica regime fiscale da vendor con revisione staff ed email | P2 | 6 | TAX.2 | Parità con VTO 2.4 |

## 4.4 Casi di regressione obbligatori

1. Esempio: commissione 20% su merce netta 100 €, lordo 122 €: commissione 20,00; fee 122 × 4% + 2 = 6,88; netto vendor 95,12.
2. Caso FEE-NETTO: la fee si calcola sul lordo (9,3424 arrotondato), non sul netto già scalato.
3. Nessuna IVA sulla commissione e nessuna IVA sulla fee (regime fiscale attuale della piattaforma).
4. Regola assente → errore bloccante, mai commissione 0 silenziosa (caso REGOLA-ZERO).
5. La percentuale congelata nell'ordine è quella valida alla data dell'ordine (caso PERC-CONGELATA).
6. Pagamento senza split non è possibile: un solo flusso di incasso (caso NO-SPLIT).
7. Il cambio di regime della piattaforma (es. IVA sulle commissioni dopo un cambio di sede) è
   una configurazione di `PlatformOperator`, non una modifica di codice.
