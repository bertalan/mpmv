# Regulatory check, November 2026

Checked on 2026-11-07 against the spec on `main` (spec 00, 01, 04, 05, 07, 09, 10, 11, 12, 92).
This is an engineering check of the spec, not legal advice: every row marked ⚖️ in the spec, and
every UNCERTAIN row below, needs confirmation by legal counsel or the tax advisor. The WCAG 2.1 →
2.2 change was handled separately (spec 14, PR #47) and is not repeated here.

Context assumed: an online marketplace operated from Italy, small at launch (micro or small
enterprise under Recommendation 2003/361/EC), selling goods B2C and B2B across the EU, plus the UK
and Switzerland; Stripe Connect with separate charges and transfers; first vertical motorcycle
gear and parts. Owner decision of 2026-11-07: the platform issues no invoices and keeps no
bookkeeping, only non-fiscal documents (spec 00).

Status values: **OK** (the spec claim is correct), **OUTDATED** (the claim was right once or is
wrong now), **UNCERTAIN** (could not be confirmed from a primary source, or depends on an
interpretation), **MISSING** (a mandatory obligation the spec did not cover).

## Top findings by legal risk

1. **Deemed supplier (VAT).** If a non-EU vendor sells goods already in the EU, or goods are imported
   in consignments of €150 or less, the platform itself becomes the VAT supplier (article 14a of
   the VAT directive, widened on 2027-01-01). The UK makes the marketplace liable for consignments
   of £135 or less sold to UK consumers, and Switzerland does so from CHF 100,000 of small
   consignments. The spec allows non-EU vendors and sells to the UK and Switzerland, but it had no
   rule for any of this. This conflicts with the no-invoicing principle: new D-21, CMP.13 and
   owner questions.
   **Follow-up (2026-11-07):** D-21 is decided. Only EU-established vendors shipping from inside
   the EU at go-live (CMP.13). UK consumer consignments of £135 or less become lead-only requests
   to the vendor (CMP.14, tax advisor to confirm). A Swiss counter with alerts (CMP.15). The
   opening to non-EU vendors is designed as P2 rows CMP.17 to CMP.22.
2. **GPSR marketplace duties (article 22).** These apply since 2024-12-13 regardless of size: Safety
   Gate registration, points of contact, 2 working days for authority orders, 3 working days for
   notices, and Safety Gate checks. The spec had only the product fields. CMP.3 is extended.
3. **DSA baseline duties apply to every platform.** Articles 11–14 and 16–18 have no small-company
   exemption, but CMP.5 was P2 (after go-live). It is now P1.
4. **Marketplace EPR checks.** These are Batteries Regulation article 62(6) (from 2025-08-18) and
   PPWR article 45(4) (from 2026-08-12), plus national laws in the destination countries. In
   France an unchecked marketplace takes on the seller's EPR obligations; Germany and Spain also
   have marketplace checks. The spec had none of this: new CMP.11.
5. **Consumer information now in force but missing.**
   - The harmonised legal guarantee notice (ECGT and Implementing Regulation 2025/1960, from
     2026-09-27).
   - The online-marketplace information of article 49-bis of the Codice del Consumo.
   - The Italian labels of the withdrawal button (D.Lgs. 209/2025).

   CMP.6 and ORD.8 are updated.
6. **VAT records of facilitated supplies** (article 242a VAT directive; 10 years). These apply to
   every facilitating platform and were missing: new CMP.12.
7. **AI Act article 50(4)** applies from 2026-08-02. Realistic AI-generated or AI-edited product
   images must be disclosed. CMP.8 moves from P3 to P1.
8. **FatturaPA specification 1.9 added RF20** (from 2025-04-01), but VEN.12 code accepts RF01–RF19
   only. This is already in code: new VEN.18.
9. **Garante privacy provision 284/2026 on email tracking pixels** (compliance from about
   2026-10-29). Transactional emails must carry no tracking pixel without consent: CMP.4 and ARC.16.
10. **P2B article 4.** Terminating a vendor needs 30 days' notice and reasons. The spec had only the
    15-day notice of changes. Spec 04 and CMP.5 are updated.

## Claims in the spec and missing obligations

| # | Topic | Legal reference (version, date) | Status | What changed or was found | Impact on spec rows | Action |
|---|---|---|---|---|---|---|
| 1 | DAC7 thresholds | Directive (EU) 2021/514; IT D.Lgs. 32/2023 | OK | Excluded seller of goods: fewer than 30 sales **and** €2,000 or less. Annual report by 31 January (AdE XML schema DPI 1.0, provvedimenti 2023-11-20 and 2024-01-30; no newer schema found, UNCERTAIN). Sellers get their data by the same date. Only EU-resident sellers are reportable | CMP.1 | ⚖️ removed from the threshold; deadline, schema and seller notice added to the text and done-when |
| 2 | DAC8 / DAC9 | Directive (EU) 2023/2226 (IT D.Lgs. 194/2025), from 2026-01-01; directive (EU) 2025/872 | OK (n/a) | Crypto-asset providers and Pillar Two only | none | Noted under "not applicable" in spec 11 |
| 3 | OSS threshold | Directive 2006/112/EC art. 59c (since 2021-07-01) | OK | €10,000 EU-wide | TAX.8 | none |
| 4 | Deemed supplier, EU | VAT directive art. 14a(1)–(2); widened from 2027-01-01 by directive (EU) 2025/516 | MISSING | Non-EU vendor with goods in the EU, or imports of €150 or less, make the platform the VAT supplier. ViDA dropped the mandatory IOSS for platforms | spec 05 decision tree, VEN.12, ORD.3 | D-21 decided: EU-established vendors only (CMP.13); opening designed as P2 CMP.17 to CMP.22 |
| 5 | VAT records | VAT directive art. 242a; Reg. 282/2011 art. 54b–54c | MISSING | Every facilitating platform keeps supplier and transaction data for 10 years, available electronically | CMP.4 retention | New CMP.12 |
| 6 | ViDA timeline | Directive (EU) 2025/516, Reg. 2025/517, Impl. Reg. 2025/518 (OJ 2025-03-25, in force 2025-04-14) | UNCERTAIN → specified | Domestic e-invoicing mandates allowed without derogation since 2025-04-14; art. 14a widened 2027-01-01; Single VAT Registration 2028-07-01; intra-EU B2B e-invoicing in EN 16931 with digital reporting 2030-07-01; national systems aligned by 2035-01-01 | CMP.2 said "ready for ViDA" | CMP.2 no longer issues invoices; ViDA dates listed in the CMP.10 seed obligations (vendor duty) |
| 7 | National e-invoicing mandates (vendor duty) | IT (since 2019; derogation to 2027-12-31, Council Impl. Decision 2024/3150); BE law of 2024-02-06; PL KSeF; FR; DE (Wachstumschancengesetz); HR (Fiscalization 2.0) | OK (BE, DE, HR from secondary sources; PL, FR from official sites) | BE B2B 2026-01-01 (Peppol). PL KSeF 2026-02-01 for large taxpayers, 2026-04-01 for others, micro 2027-01-01. FR receipt for all and issuing for large and mid-size from 2026-09-01, issuing for SMEs from 2027-09-01. DE receipt 2025-01-01, issuing 2027-01-01 above €800,000 and 2028-01-01 for all. HR B2B 2026-01-01 | none (the platform issues no invoices) | Listed in the CMP.10 seed obligation INVOICE; EN 16931 order-data export proposed as an owner question (P2, not added) |
| 8 | FatturaPA version | AdE Specifiche tecniche 1.9 (from 2025-04-01) and 1.9.1 (usable from 2026-05-15); schema FPR12 v1.2.3 | OUTDATED | 1.9 added RF20 (cross-border SME franchise, Directive 2020/285, IT D.Lgs. 180/2024) and TD29. The spec said "FatturaPA 1.2.x" and RF01–RF19 | VEN.12 (done), spec 04 | Spec 04 updated; new VEN.18 adds RF20 in code |
| 9 | Commission e-invoicing via SDI | — | Superseded | Owner decision: no invoicing by the platform | CMP.2, ADR-0003, spec 91 external costs | CMP.2 is now a non-fiscal statement and export; `EInvoiceChannel` and the SDI intermediary are dropped |
| 10 | EU VAT rate changes | National laws; EC TEDB | OK | FI 25.5% (2024-09-01), SK 23% (2025-01-01), EE 24% (2025-07-01), RO 21% (2025-08-01). No standard-rate change found for 2026 (secondary sources) | TAX regression T5 | none; TAX.6 imports from TEDB |
| 11 | Customs reform | Council Reg. (EU) 2026/382 (2026-02-11); Impl. Reg. 2026/1200; UCC Reg. (EU) 2026/2108 and Del. Reg. 2026/2152 | MISSING (only for imports) | €150 duty relief removed; €3 duty per item on low-value B2C consignments 2026-07-01 → 2028-07-01; €2 handling fee per item from 2026-11-01. Who owes the €3 in a platform sale is UNCERTAIN | Only if non-EU vendors ship into the EU | No imports at go-live (D-21, CMP.13); CMP.19 for the opening |
| 12 | Italian parcel fee | L. 199/2025 art. 1 c. 126–128 | UNCERTAIN | €2 per consignment of €150 or less from outside the EU; start postponed to 2026-12-01 (DL 162/2026), maybe 2027-01-01, possibly abolished by the 2027 budget bill (secondary sources) | Only for imports | Watch; covered by D-21 |
| 13 | UK marketplace VAT | VATA 1994 s.5A (since 2021-01-01) | MISSING | The marketplace is liable for consignments of £135 or less sold to UK consumers, and for goods in the UK sold by overseas sellers. A 2026 HMRC paper extends liability to UK-established sellers above £90,000 (start date UNCERTAIN). The £135 customs relief goes by October 2028 at the latest. UK platform reporting does not apply to an EU platform | Markets "EU, UK" (spec 00), export rule in spec 05 | D-21 decided: lead-only requests to the vendor (CMP.14), tax advisor confirms (L) |
| 14 | Swiss platform VAT | MWSTG art. 20a (from 2025-01-01) | MISSING | The platform becomes the supplier of goods from CHF 100,000 a year of small consignments | Markets, spec 05 | D-21 decided: counter with alerts (CMP.15), response with the tax advisor (CMP.16) |
| 15 | Forfettario threshold | L. 190/2014 as amended by L. 199/2025 | OK | €85,000; immediate exit above €100,000 | Q-50, spec 04 regression 3 | none |
| 16 | PSD2 / PSD3 / PSR | Provisional agreement 2025-11-27; COREPER 2026-04-22; ECON 2026-05-05; plenary expected about December 2026 | UNCERTAIN | Not yet in the OJ; PSR applies about 21 months after publication. The commercial agent exclusion is narrowed for marketplaces acting for both sides (secondary sources). Stripe is the licensed provider | D-08 | D-08 text updated; decision still open |
| 17 | eIDAS and signatures | Reg. (EU) 910/2014 as amended by Reg. (EU) 2024/1183; CAD art. 20 c. 1-bis | OK | Art. 25 unchanged. EUDI wallets due by 2026-12-24, with free QES for natural persons (member states may limit this to non-professional use). CAD: QES/AdES or an AgID process meets written form; a simple signature is assessed freely. IT-Wallet certification status UNCERTAIN | VEN.14, VEN.17, spec 11 | Spec 04 VEN.17 text: wallet QES accepted as PAdES/CAdES |
| 18 | DSA scope for a small platform | Reg. (EU) 2022/2065 arts 11–18, 15(2), 19, 29 | OUTDATED (priority) | Arts 11–14 and 16–18 apply to everyone; arts 15 and 19–32 do not apply to micro and small platforms until 12 months after they stop being small | CMP.5 was P2 | CMP.5 → P1 (10 h) with points of contact, terms, statement of reasons, criminal offences |
| 19 | DSA arts 30–32 | Reg. (EU) 2022/2065 | MISSING (by design) | KYBC with self-certification, display on offers, 6-month retention after the relationship; compliance by design; informing buyers of the last 6 months | CMP.5 had KYBC in one line; VEN.5 (done) does not ask for the self-certification | New CMP.11; CMP.10 collects the self-certification as an activation condition |
| 20 | P2B | Reg. (EU) 2019/1150 arts 3, 4, 5, 7, 9, 11, 12 | OK, partly MISSING | 15-day notice OK. Missing: art. 4 reasons and 30-day termination notice, art. 7 differentiated treatment, art. 9 data access. Arts 11–12 not required for small enterprises. The Digital Omnibus (proposed 2025-11-19, Council mandate 2026-10-21) would repeal parts; not law | spec 04 P2B paragraph, CMP.5 | Both updated |
| 21 | GPSR | Reg. (EU) 2023/988, applies 2024-12-13; arts 19, 22 | OK (date), MISSING (art. 22) | Art. 19 needs a product picture; art. 22 marketplace duties as in top finding 2 | CMP.3 | CMP.3 extended to 12 h |
| 22 | Online withdrawal function | Directive (EU) 2023/2673 (art. 11a of 2011/83), from 2026-06-19; IT D.Lgs. 209/2025 (GU 2026-01-08), art. 54-bis Codice del Consumo | OK (date), OUTDATED (⚖️ open) | Italy has transposed it. Label "recedere dal contratto qui", confirmation "conferma recesso", acknowledgement with date and time; art. 49(1)(h) pre-contract information | CMP.6, ORD.8, spec 07 | Labels added; ⚖️ kept only for DE/FR/ES wording |
| 23 | Omnibus (price, reviews, marketplace info) | Directive (EU) 2019/2161; IT D.Lgs. 26/2023, arts 17-bis, 22, 23, 49-bis Codice del Consumo | OK, partly MISSING | 30-day lowest price and review transparency are in the spec. Art. 49-bis (trader status, split of obligations, ranking) was missing | CMP.6, CMS.6 | CMP.6 extended |
| 24 | ECGT (green claims, durability) | Directive (EU) 2024/825, from 2026-09-27; Impl. Reg. (EU) 2025/1960; IT D.Lgs. 30/2026 | MISSING | Generic environmental claims and uncertified labels banned; harmonised legal guarantee notice and durability label. The Green Claims Directive proposal is on the 2027 withdrawal list | CMP.6, CMP.8, ING.5 | CMP.6 (notice), CMP.8 (enrichment policy), CMP.10 seed GREEN |
| 25 | Product Liability Directive | Directive (EU) 2024/2853 (OJ 2024-11-18), transposition 2026-12-09; corrigendum 2026-05-07: products placed on the market after 2026-12-08; IT draft decree AG 434 | MISSING | Software and AI are products; a platform is liable like a distributor if it cannot identify the economic operator within one month | none | CMP.11 (identify the vendor on request, no "platform's own" presentation) |
| 26 | AI Act art. 50 and art. 4 | Reg. (EU) 2024/1689 as amended by Reg. (EU) 2026/1744 (OJ 2026-07-24); art. 50 from 2026-08-02; art. 50(2) grace to 2026-12-02 for systems on the market before 2026-08-02 | OUTDATED (CMP.8 was P3, "assess") | 50(2) is the provider's duty. 50(4) requires the deployer to disclose realistic AI images. AI text reviewed under editorial responsibility needs no label. Art. 4 is now "take measures to support" AI literacy. Labelling code of practice final 2026-06-10 | CMP.8 | CMP.8 → P1 (4 h) |
| 27 | EAA | Directive (EU) 2019/882, from 2025-06-28; IT D.Lgs. 82/2022; AgID guidelines v1.0 (2026-03-04) | OK | Microenterprise exemption for services confirmed; AgID asks for a yearly assessment | CMP.7, D-15 | WCAG text handled elsewhere; yearly assessment noted for the owner |
| 28 | GDPR, cookies | Garante provision 231/2021; ePrivacy Regulation proposal withdrawn (OJ C/2025/5423) | OK | Scrolling is not consent; cookie wall only with an equivalent alternative; 6-month re-prompt. The Digital Omnibus cookie changes are not law | CMP.4, SEO.5 | none |
| 29 | Email tracking pixels | Garante provision 284 of 2026-04-17 (GU 2026-04-29) | MISSING | Consent is needed for tracking pixels in email, except for anonymous aggregate statistics and service messages | ARC.16, CMP.4 | CMP.4 text; ARC.16 sends no pixels |
| 30 | Data Act | Reg. (EU) 2023/2854, from 2025-09-12; art. 3(1) for products placed after 2026-09-12 | MISSING (vendor duty) | Sellers of connected products give pre-contract data information | none | CMP.10 seed DATA; CMP.11 interface fields |
| 31 | Geo-blocking | Reg. (EU) 2018/302 | OK | In force; evaluation running, no proposal yet | CMP.6 | none |
| 32 | Batteries Regulation | Reg. (EU) 2023/1542; art. 62(6) and EPR from 2025-08-18; labels from 2026-08-18 or later; QR code and passport from 2027-02-18; due diligence 2027-08-18 (Reg. 2025/1561); IT D.Lgs. 29/2026 | MISSING | Platforms within DSA Section 4 obtain registration numbers and self-certification. Moto: SLI, portable (intercoms, heated gear), LMT | none | CMP.11; CMP.10 seed BATTERIES; size exemption question (L) |
| 33 | PPWR | Reg. (EU) 2025/40 (OJ 2025-01-22), from 2026-08-12; art. 45(4) | MISSING | Same platform check as batteries; authorised representative per member state of distance sale (Omnibus suspension in trilogue, not law); empty-space ratio 2030 | none | CMP.11; CMP.10 seed PACKAGING |
| 34 | WEEE and national marketplace EPR | Dir. 2012/19/EU; IT D.Lgs. 49/2014 and art. 178-quater D.Lgs. 152/2006; DE ElektroG §6(2), VerpackDG §13(4), BattDG; FR L.541-10-9; ES RD 1055/2022, RD 27/2021 | MISSING | DE checks; FR marketplace liable unless it holds the sellers' unique identifiers; ES platform steps in for foreign producers without a representative. Application to a foreign marketplace is UNCERTAIN | none | CMP.11 configurable per country and stream; question (L) |
| 35 | Tyres and oils (Italy) | D.Lgs. 152/2006 arts 228 and 236; D.M. 182/2019; CONOU | MISSING (vendor duty) | PFU contribution shown on invoices; CONOU membership | none | CMP.10 seeds TYRES, OILS |
| 36 | Moto product rules | Reg. (EU) 2016/425 (EN 17092); UNECE R22.06; Reg. (EU) 168/2013 | MISSING (vendor duty) | Helmets need R22 approval; protective clothing is PPE; regulated parts need type approval | none | CMP.10 seed CONFORMITY |
| 37 | NIS2 | Dir. (EU) 2022/2555; IT D.Lgs. 138/2024 | OK (n/a) | Marketplaces in Annex II, medium and large only | none | "Not applicable" note in spec 11; reassess on growth |
| 38 | CRA | Reg. (EU) 2024/2847; reporting 2026-09-11, full 2027-12-11 | OK (n/a) | Platform software out of scope; a mere marketplace is not a distributor; vendors of connected products are distributors from 2027 | none | "Not applicable" note; CMP.10 item to add in 2027 |
| 39 | Clausole vessatorie | Codice civile arts 1341–1342; CAD art. 20 | OK | Unchanged | VEN.13–VEN.17 | none |
| 40 | Accessibility WCAG | EN 301 549 V4.1.1 | — | Handled by PR #47 | — | — |

## Already in code and now non-compliant or superfluous

- `apps/vendors/models.py` and migration `0004_legal_identity`: `fiscal_regime` accepts RF01–RF19
  only, and the check constraint refuses RF20. **Non-compliant** with FatturaPA 1.9 for vendors under
  the cross-border SME franchise: VEN.18.
- VEN.5 activation (PR #37) does not collect the DSA article 30(1)(e) self-certification. It is
  needed before a vendor sells (formally from DSA Section 4 once the platform is no longer small,
  and required now by GPSR article 22(10) for vendors of consumer products): CMP.10 and CMP.11.
- `apps/core/models.py` `PlatformOperator.einvoice_channel` (ARC.7) was **superfluous** under
  the no-invoicing decision. Removed by ARC.17 (migration `core/0018`).

## Sources

EU law and Commission:
- https://eur-lex.europa.eu/eli/reg/2022/2065/oj/eng
- https://eur-lex.europa.eu/eli/reg/2023/988/oj/eng
- https://digital-strategy.ec.europa.eu/en/policies/platform-business-trading-practices
- https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:52023DC0525
- https://www.consilium.europa.eu/en/press/press-releases/2026/10/21/digital-rulebook-council-agrees-position-to-simplify-data-protection-privacy-and-cyber-reporting-rules/
- https://eur-lex.europa.eu/eli/reg_impl/2025/1960/oj/eng
- https://commission.europa.eu/publications/2027-commission-work-programme-and-annexes_en
- https://eur-lex.europa.eu/eli/dir/2024/2853/oj/eng
- https://eur-lex.europa.eu/eli/dir/2024/2853/corrigendum/2026-05-07/oj/eng
- https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng
- https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act
- https://digital-strategy.ec.europa.eu/en/news/commission-publishes-code-practice-marking-and-labelling-ai-generated-content
- https://digital-strategy.ec.europa.eu/en/policies/geoblocking
- https://www.europarl.europa.eu/legislative-train/theme-connected-digital-single-market/file-jd-e-privacy-reform
- https://www.europarl.europa.eu/legislative-train/theme-protecting-our-democracy-upholding-our-values/file-digital-fairness-act
- https://taxation-customs.ec.europa.eu/taxation/vat/vat-digital-age-vida_en
- https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202500516
- https://eur-lex.europa.eu/eli/reg_impl/2011/282
- https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32019R2026
- https://trade.ec.europa.eu/access-to-markets/en/news/eu-applies-eu3-customs-duty-item-low-value-e-commerce-consignments
- https://www.consilium.europa.eu/en/press/press-releases/2025/12/12/customs-council-agrees-to-levy-customs-duty-on-small-parcels-as-of-1-july-2026/
- https://taxation-customs.ec.europa.eu/customs/eu-customs-reform_en
- https://eur-lex.europa.eu/eli/reg/2026/2108/oj/eng
- https://www.europarl.europa.eu/legislative-train/theme-an-economy-that-works-for-people/file-revision-of-eu-rules-on-payment-services
- https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32024R1183
- https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32023R1542
- https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32025R0040
- https://www.consilium.europa.eu/en/press/press-releases/2026/10/21/simplification-council-agrees-negotiating-stance-to-cut-red-tape-for-small-packaging-producers/
- https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32024R2847
- https://digital-strategy.ec.europa.eu/en/policies/cra-summary
- https://eur-lex.europa.eu/eli/reg/2016/425/oj/eng
- https://eur-lex.europa.eu/eli/reg/2013/168

Italy:
- https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:2025-12-31;209
- https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:2026-02-20;30
- https://www.brocardi.it/codice-del-consumo/parte-iii/titolo-iii/capo-i/sezione-ii/art49bis.html
- https://www.affarieuropei.gov.it/it/normativa/legge-di-delegazione-europea/legge-delegazione-europea-2025/
- https://www.agenziaentrate.gov.it/portale/specifiche-tecniche-versione-1.9.1-%C2%A0-utilizzabili-dal-15-maggio-2026-
- https://www.agenziaentrate.gov.it/portale/normativa-prassi-e-regole-tecniche-fatture-elettroniche
- https://www.fatturapa.gov.it/it/norme-e-regole/documentazione-fattura-elettronica/formato-fatturapa/
- https://www.agenziaentrate.gov.it/portale/scambio-automatico-di-informazioni-comunicate-dai-gestori-di-piattaforme-dac7/specifiche-tecniche
- https://www.garanteprivacy.it/home/docweb/-/docweb-display/docweb/9677876
- https://www.garanteprivacy.it/home/docweb/-/docweb-display/docweb/10241943
- https://www.agid.gov.it/en/news/european-accessibility-act-eaa-agid-publishes-guidelines-digital-accessibility
- https://www.gazzettaufficiale.it/eli/id/2026/03/06/26G00046/sg
- https://www.brocardi.it/codice-dell-ambiente/parte-quarta/titolo-i/capo-i/art178quater.html
- https://www.mase.gov.it/portale/documents/d/guest/testo-a-fronte-dm182_2019_-pdf
- https://www.acn.gov.it/portale/en/faq/nis/registrazione
- https://docs.italia.it/italia/piano-triennale-ict/codice-amministrazione-digitale-docs/it/v2018-09-28/_rst/capo2_sezione1_art20.html

Other countries:
- https://www.gov.uk/guidance/vat-and-overseas-goods-sold-to-customers-in-the-uk-using-online-marketplaces
- https://www.gov.uk/government/publications/vat-online-marketplace-liability-extension/extending-vat-online-marketplace-liability
- https://www.gov.uk/guidance/reporting-rules-for-digital-platforms
- https://www.gov.uk/government/publications/reforming-customs-rules-for-low-value-imports
- https://www.estv.admin.ch/de/mwst-versandhandel-und-plattformbesteuerung
- https://www.bazg.admin.ch/de/regelung-mwst-versandhandel-und-plattformbesteuerung
- https://www.gesetze-im-internet.de/verpackdg/BJNR0CF0B0026.html
- https://www.ecologie.gouv.fr/sites/default/files/documents/FAQ%20place%20de%20march%C3%A9%20L541-10-9_anglais.pdf
- https://www.boe.es/buscar/pdf/2022/BOE-A-2022-22690-consolidado.pdf
- https://ksef.podatki.gov.pl/informacje-ogolne-ksef-20/podstawy-prawne-oraz-kluczowe-terminy/
- https://www.economie.gouv.fr/tout-savoir-sur-la-facturation-electronique-pour-les-entreprises
- https://www.impots.gouv.fr/sites/default/files/media/1_metier/2_professionnel/EV/2_gestion/290_facturation_electronique/guide_pratique_facturation_electronique.pdf

Secondary sources used only to corroborate or for very recent events:
- https://www.twobirds.com/it/insights/2026/italy/la-nuova-funzione-digitale-di-recesso
- https://www.twobirds.com/en/insights/2026/european-commission-adopts-final-guidelines-on-ai-act-article-50-transparency-obligations-first-impr
- https://www.meijburg.com/news/eu-proposal-vat-digital-age-package-formally-adopted
- https://www.pwc.nl/en/insights-and-publications/tax-news/vat/vat-in-the-digital-age-vida-adopted-by-the-council.html
- https://www.vatupdate.com/2025/03/04/italy-receives-derogation-for-e-invoicing-until-2027/
- https://www.vatcalc.com/global/2026-global-vat-rate-changes/
- https://www.quotidianopiu.it/dettaglio/16444854/rinviata-al-1-dicembre-2026-la-tassa-nazionale-sui-mini-pacchi
- https://www.invoicenavigator.eu/deadlines
- https://www.eadtrust.eu/en/blog/december-2026-deadline-eudi-wallet/
- https://www.pandslegal.it/insights/dlgs-292026-nuovo-regime-ue-batterie
- https://www.it-recht-kanzlei.de/weee-elektrogeraete-pruefpflicht-verlaengerung-uebergangsfrist.html
