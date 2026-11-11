# Tax decision reasons and exemption nature codes

For the owner and the tax advisor. Every zero-VAT outcome of the goods tax engine
(`resolve_goods_tax`, TAX.3) carries a **reason** and, where the Italian e-invoice requires one, a
**nature code** (FatturaPA `Natura`, N1 to N7). The platform issues no invoices (spec 00): the code
travels on the decision and in the order snapshot, so the vendor's own invoice and the reports use
the same value. The advisor offered to check this table before production (D-28).

## Mapping

| Reason (`TaxDecision.reason`) | When it applies | Nature code | Legal reference | Status |
|---|---|---|---|---|
| `standard_destination` | B2C (or an unverified business) delivered in another member state: OSS, destination rate | none (VAT charged) | Directive 2006/112/EC art. 33 (distance sales), OSS scheme | D-01 decided |
| `domestic_vendor_country` | Delivery in the vendor's own country, `domestic_only` vendor, or a business with a number of the vendor's own country delivered in another member state | none (VAT charged, vendor-country rate) | Art. 32 (place of supply is departure); art. 138 needs a number of another member state | D-26.1 and D-26.2 confirmed by the advisor (D-28) |
| `reverse_charge` | VIES-valid business with a number of another member state than the vendor's, goods delivered in a member state other than the vendor's | **N3.2** (intra-EU supply) | Directive art. 138 (IT art. 41 D.L. 331/93); buyer accounts for VAT under art. 196 | Confirmed (D-28) |
| `export` | Delivery outside the EU | **N3.1** (exports) | Directive art. 146 (IT art. 8 DPR 633/72) | Proposed, advisor to check |
| `outside_vat_territory` | Delivery to a territory outside the VAT area (Canary Islands, Livigno, Aland, Saint-Martin, Saint-Barthelemy) | **N2.1** (not subject, arts. 7 to 7-septies) | Directive art. 6; IT arts. 7 to 7-septies DPR 633/72 | Proposed, advisor to check |
| `art74_publishing` | Italian publisher exempt under art. 74 c.1 lett. c | **N2.2** (not subject, other cases) | IT art. 74 c.1 lett. c DPR 633/72 | Proposed, advisor to check |
| `flat_rate_vendor` | Italian forfettario vendor (fiscal regime RF19) | **N2.2** | IT art. 1 c. 54-89 L. 190/2014 | Proposed, advisor to check |
| `exempt_other` | Any other exemption the staff record on the vendor's tax profile | The profile's **exemption nature** (one of N1 to N7); required for Italian vendors | The profile's translated legal note (the legal reference text, required and non-empty) and law country | D-26.4 and D-28 |

Codes the engine accepts for `exempt_other`: N1, N2.1, N2.2, N3.1 to N3.6, N4, N5, N6.1 to N6.9, N7.

## Rules around the table

- An Italian vendor's `exempt_other` profile needs both the nature code and the legal note text;
  otherwise the sale is blocked with an `IncompleteTaxProfile` message (never a silent 0%).
- A vendor of another country needs the legal note and the law country; the nature code is optional.
- Taxed outcomes carry no nature code.
- Not yet in the engine (backlog rows proposed in D-28): the intra-EU triangulation (N3.2 on art. 58
  D.L. 331/93, Italian vendors only) and the declaration of intent (N3.5).

## Questions for the advisor

1. Are N3.1, N2.1 and N2.2 the right codes for exports, territories outside the VAT area and the
   two Italian exemption reasons?
2. Which nature does an art. 74 publisher use on its own invoices: N2.2 or another code?
3. Should `outside_vat_territory` use N2.1, or N3.1 (treated like an export) for the territories
   that sit outside the EU customs area too?
