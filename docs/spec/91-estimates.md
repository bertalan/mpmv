# 91 — Estimates and release plan

## 91.1 How to read the hours

- Micro-task hours (see [90-backlog.md](90-backlog.md)) cover **development and automated
  tests**. They assume a senior developer who knows Django and works with an agentic AI
  assistant (Claude Code or similar).
- They do not include cross-cutting costs, which are added below.
- They are top-down estimates per micro-task, not a quote: they must be reviewed after phase F0, when
  the project skeleton exists and the actual velocity can be measured. The measured velocity
  with agents is in §91.6.

## 91.2 Go-live total (P1 only)

| Item | Hours | How it is calculated |
|---|---:|---|
| P1 micro-tasks | 1,559 | Sum from the generated backlog |
| Integrated and end-to-end acceptance testing, exploratory tests | 100 | ~10% of P1 |
| Mandatory human review of tax, payments, ledger, orders, security | 50 | ~15% of the hours for TAX, PAY, VEN, ORD, SEC |
| Coordination, ADRs, meetings with tax advisor and legal counsel | 80 | ~8% of P1 |
| **Subtotal** | **1,789** | |
| Contingency for unforeseen issues (10–25% of P1) | 156–390 | Tax and payment domain, dirty legacy data |
| **Go-live total** | **≈ 1,945–2,179** | |

The SHP.12 implementation (#54) added the SHP.12 row it built and the SHP.13 and SHP.14 rows it
found missing (+34 h of P1). Coherence check 5 (2026-11-07) added ARC.17 (1 h, e-invoicing
leftovers), ARC.18 (2 h, architecture guards), CAT.12 (4 h, audited product edits), CORE.14 (3 h,
vendor key policy), TAX.11 (2 h, shared postcode grammar for tax territories) and CMP.25 (4 h,
activation gate for the DSA article 30 data), grew VEN.18 (2 → 3 h, RF20 in the tax request) and
narrowed SHP.2 to the legacy cases (8 → 6 h): +15 h of P1.

The transactional email task is split in two (2026-11-08): ARC.16 keeps the code (outbox,
`EmailSender` with test and console backends, templates, bounce ingestion) and depends on ARC.9
only (8 → 5 h); the new row ARC.19 (3 h, depends on ARC.16 and ARC.13) wires the EU provider on
STAGE and LIVE once owner question Q-48 picks it, with DKIM, SPF and DMARC, the provider webhook and
a deliverability check: +1 task, 0 h of P1. Eleven P1 tasks (94 h) that waited for STAGE only
through ARC.16 no longer do.

STAGE moves to a single VPS and gains the abuse-test row ARC.21 (2026-11-08): +1 task,
+8 h of P1. The multi-arch image build stays inside ARC.13 at 11 h. The forecast below still
counts the earlier totals.

The public STAGE demo mode (owner decision 2026-11-08) adds the row ARC.22: +1 task, +6 h of P1.

The owner's decision D-07 (2026-11-07: descriptive `/{taxonomy}/{term}/` URLs, legacy tag URLs
moved with the staggered product URL mechanism) widens CAT.8, MIG.6 and MIG.11 at the same hours.
The product grid block leaves CMS.2 (10 → 8 h, now depending on CMS.1 only) for the new row CMS.10
(3 h, depends on CMS.2 and CAT.8), and CMS.8 no longer depends on ARC.15: +1 h of P1.

The events module (2026-11-07, [15](15-events.md)) adds EVT.1 to EVT.15 (113 h of P1: event
pages and moderation, recurrence, vendor area and API, public pages, closure-aware dispatch date,
RSS and ICS feeds, JSON-LD, emails, geocoding, map picker, tile proxy and public maps) and EVT.16
and EVT.17 (14 h of P2: follower emails, and the Google geocoder and map layer that the owner
postponed until after go-live). It supersedes owner decision Q-61 for events.

The owner's answers on events (Q-72 to Q-75, same day) changed P1 by +10 h: vendor holiday
closures leave the events module and become a vendor setting, VEN.20 (12 h: closure schedule,
notices, dispatch date at checkout); EVT.7 keeps only the storefront events section (5 → 3 h);
EVT.8 becomes multilingual events, the D-18 exception for vendor-authored events (8 h, replacing
the closure-aware dispatch date). P2 changes by +2 h: EVT.16 becomes event notifications in the
customer's account preferences (6 → 4 h) and depends on the placeholder ORD.11 (4 h, customer
preferences and request history, scope to be defined with the owner); both are to be
re-estimated once the owner defines that section.

The theme system (2026-11-07, [14](14-frontend-themes.md)) replaced the 24 h CMS.7 row with
THM.1 to THM.10 (70 h: framework, token pipeline, `base` theme, components, quality gates and the
first vertical theme) and added CMP.9 (3 h, WCAG 2.2 accessible authentication): +49 h of P1.
The owner's answers on the themes (same day) added CMS.8 (5 h, image source, licence and credit)
and CMS.9 (4 h, launch countdown block): +9 h of P1. Vendor branding (THM.11, 12 h) and plan
entitlements and fees (VEN.19, 8 h) are P2.

The regulatory check of 2026-11 ([report](../dev/regulatory-check-2026-11.md)) and the
no-invoicing decision changed P1 by +68 h: CMP.2 becomes a non-fiscal statement (12 → 8 h), CMP.3
adds the GPSR marketplace duties (8 → 12 h), CMP.5 moves from P2 to P1 (6 → 10 h), CMP.6 grows
(8 → 10 h), and the new rows CMP.8 (4 h), CMP.10 (24 h), CMP.11 (16 h), CMP.12 (4 h), CMP.13 (6 h)
and VEN.18 (2 h) are added. P2 loses the 6 h of CMP.5.

The owner answers on deemed-supplier VAT, EPR checks and legal wording (D-21, 2026-11-07)
changed P1 by +24 h: CMP.13 becomes the vendor establishment gate (6 → 5 h), CMP.11 adds the
owner-approved activation per country (16 → 19 h), and the new rows CMP.14 (12 h, UK lead-only
requests), CMP.15 (4 h, Swiss counter) and CMP.24 (6 h, approval of legal wording) are added. P2
gains 69 h: CMP.16 (3 h, Swiss threshold response), the opening to non-EU vendors CMP.17 to
CMP.22 (58 h) and CMP.23 (8 h, EN 16931 order-data export).

The owner's answers on vendors, access and the catalogue (Q-16 to Q-18, Q-29 to Q-34, Q-55,
Q-56, 2026-11-07) changed P1 by +48 h: CORE.8 grows (10 → 13 h: platform staff permission,
account unlock), CORE.9 grows (12 → 16 h: owner-only storefront profile with post-moderation),
CMP.4 grows (10 → 12 h: erasure for a closed vendor's owner), and the new rows CORE.12 (4 h,
per-key API rate limit), CORE.15 (6 h, secure action links for approval emails), OPS.5 (3 h, Django admin on a restricted network), VEN.21 (2 h, reopen a
closed vendor), VEN.22 (8 h, public "Sell with us" application), CAT.13 (8 h, internal identity
and the vendor SKU as a label) and CAT.14 (8 h, catalogue integrity rules) are added. P2 gains
50 h: CAT.15 (6 h, other sellers of the same article), and cross-vendor bundles with discount
funding: CAT.16 (14 h, bundle and funding split), CAT.17 (18 h, checkout, ledger and returns) and
CAT.18 (12 h, vendor acceptance of staff-assigned shares). The answer to Q-91 (new SKU-free
product URLs with one 301 from every legacy URL) grows MIG.6 from 6 to 8 h, and its refinement (switch staggered
by traffic after go-live, with gates and rollback) adds MIG.11 (6 h): +8 h of P1.

The owner's answers on editor roles and customer legal pages (Q-58 and Q-42, 2026-11-07) changed
P1 by +8 h: CORE.8 grows (13 → 15 h: our own editor roles instead of Wagtail's default groups) and
the new row I18N.9 (6 h, customer legal and checkout pages in five languages at go-live) is added.

The owner's decisions on the backoffice, setup and imports (BO-1 to BO-12, 2026-11-09) changed P1
by +80 h: the Manager shell CORE.17 (18 h), the staff role matrix CORE.18 (10 h), support
impersonation CORE.10 moved from P2 to P1 and grown (4 → 14 h: policy, full-access grants, hard
floor), marketplace configuration pages ARC.24 (17 h), the setup wizard ARC.25 (14 h), the
connector interface MIG.12 (8 h) and the operator-owned shop VEN.26 (5 h); CORE.8 shrinks (15 → 12 h) and CORE.9 shrinks (16 → 13 h)
because the commerce lists and the shell move to CORE.17. P2 gains 24 h net: ARC.26 (8 h, wizard
imports), the WooCommerce connector MIG.13 (12 h) and the WCFM connector MIG.14 (8 h), less the
4 h of CORE.10.

After go-live: P2 = 334 h and P3 = 18 h (MCP, advanced zone editor, OSS report, visitor content
translation, reviews, advanced payouts, vendor branding, customer preferences and event
notifications, Google geocoder and map layer, cross-vendor bundles…), about **385–440 h**
including contingency.

## 91.3 Comparison with the August plan

| | August plan | This review |
|---|---:|---:|
| Scope | Full WCFM parity + P1 commercial modules | Parity **with what is actually used** + 2026 compliance |
| Total hours (development, testing, migration) | 1,740–2,540 | 1,945–2,179 to go-live |
| With AI-assisted development | 1,450–2,100 | already included |

Where the difference comes from:

- **Fewer components**: no Celery/Redis/Elasticsearch and no full vendor dashboard.
- **Modules not migrated**: membership with billing, affiliate, staff, analytics.
- **Reuse of the pipelines** as Python code in the same project: no HTTP client towards PHP,
  no STAGE → LIVE promotion.
- **Agentic development** used systematically on the mechanical parts (models, CRUD, admin, parametric
  tests). The tax and payment parts remain under line-by-line human review.

Additions compared with August: online withdrawal, EAA/accessibility (WCAG 2.2 AA), operational GPSR,
MCP server, theme system with a first vertical theme.

## 91.4 Indicative schedule

The P1 critical path is short (~147 h: foundations → vendor onboarding → contract signature and
updates → obligations checklist → marketplace verification duties; the orders and payments chain
is ~126 h):
the schedule depends on team capacity more than on dependencies.

| Team | Months to go-live | Notes |
|---|---|---|
| 1 full-time senior (~150 productive hours per month) | 9–10 | Sequence F0 → F5 |
| 2 seniors (one on F2/F3, one on F1/F4/F5) | 5–6 | Coordination included in the hours |
| 1 senior + editor for content and translations | 8–9 | The editor takes MIG.7, copy review, CMS |

### Phases and milestones

| Phase | Content | Verifiable milestone |
|---|---|---|
| F0 Foundations | Repo, CI, deploy, core, identity, baseline security | STAGE online with MFA login and admin |
| F1 Catalog and import | Catalog, multilingual, pipelines for Vendor A and the other vendors, theme system and `base` theme | LIVE-equivalent catalog browsable on STAGE, fed by the pipelines |
| F2 Checkout | Tax, shipping, quotes, cart, orders, Stripe | Multi-vendor order paid in test mode with correct transfers |
| F3 Vendors and compliance | Ledger, payouts, vendor dashboard, DAC7, GPSR, GDPR, withdrawal | Pilot vendor operates on their own on STAGE |
| F4 Content and SEO | Wagtail pages, blog, taxonomy landing pages, JSON-LD, feeds, first vertical theme pages | Rich Results and Merchant feeds validated |
| F5 Go-live | Migration, parity, dress rehearsals, pentest, cutover | Third dress rehearsal with no blockers → go-live |

A milestone is closed only with a working demo on STAGE and the domain regression tests green.

## 91.5 External costs to be quoted separately

- External penetration test (SEC.6; the hours listed are for remediation).
- Tax advisor and legal counsel for the ⚖️ decisions in [92-open-decisions.md](92-open-decisions.md).
- Interface text in IT, DE, FR and ES at go-live (D-18): native-speaker review of the AI drafts for
  checkout, emails and legal/tax wording. Customer legal and checkout pages are translated at
  go-live (Q-42, I18N.9), so counsel also checks the legal texts in each language (Impressum,
  terms, privacy, withdrawal notice, shipping and returns).
- Translation of visitor content after go-live (I18N.8), with the method the owner chooses.
- CMP for cookies; transactional email provider. No SDI intermediary: the platform issues no
  invoices, and the operator's accountant invoices commissions with its own tool from the CMP.2
  statement.
- Hosting: Hetzner Cloud VPS, Arm64 recommended (LIVE: app VPS and database VPS; STAGE: one VPS for both), Hetzner Object Storage for media and backups, CDN, error tracking.
- Custom graphics and UX beyond the `base` theme and the first vertical theme (spec 14), such as a
  logo, illustrations or commissioned photography.

In exchange, the recurring costs of premium WordPress licenses disappear (WCFM Ultimate, Membership,
Affiliate, Groups & Staffs, Analytics, WPML, WPSSO Premium and add-ons) and, above all, so do the
maintenance hours for updates, incompatibilities and fixes (STAGE, test matrices, option patches).

## 91.6 Measured velocity with agents (non-binding)

This section is a forecast, not a commitment. It does not replace the hours above, which stay the
reference for a human team. It is refreshed at every coherence check.

### Measurement of 2026-11-07 (coherence check 5, after wave 6)

Source: merged pull requests and their timestamps, the generated backlog, and the plan usage meter.

- **Done:** 33 P1 tasks, worth **253 estimated hours** (waves 0–6; I18N.1 and CORE.7 are partial,
  waiting for STAGE), plus the P2 tasks ARC.8 and OPS.4 (9 h).
- **Wave 6** (CAT.2, SHP.12, TAX.2, ARC.8, OPS.4: 37 estimated hours): implementation, independent
  review and fixes ran from 12:33 to 13:10 (37 minutes); integration (merges in order, CI on each
  rebased branch, one Dependabot follow-up) ended with the last task merge at 13:57 and the progress
  update at 14:04. That is **84 minutes** for 37 estimated hours, about 26 times faster than the
  estimate, in line with waves 0–5 (one wave of 5 tasks, 36–44 hours, in 80–95 minutes).
- **Integration is now half of the wall-clock time** (37 of 84 minutes in wave 6): five branches
  touching migrations, the tenancy registry and spec tables merge one after the other.
- **Real limit:** the AI plan's weekly budget, not speed. With work stopping at 80% of the weekly
  budget, one week covers about **500 estimated hours** of agent work.

### Forecast for the rest of P1

**143 P1 tasks remain, worth 1,242 estimated hours** (142 at the measurement, plus ARC.19 from
the ARC.16 split, §91.2). At the previous measurement 134 tasks and
1,210 hours remained; since then the owner's events answers added VEN.20 and shrank EVT.7 (+10 h),
wave 6 closed 28 P1 hours, the SHP.12 implementation added SHP.12 to SHP.14 (+34 h) and coherence
check 5 added six small rows and resized two (+15 h, §91.2), and the D-07 follow-up split the
product grid out of CMS.2 into CMS.10 (+1 task, +1 h, §91.2).

| Part | Estimated hours | Done by | Forecast |
|---|---:|---|---|
| Code and tests (CAT, ORD, SHP, TAX, PAY, VEN, CMP, ING, SEO, CMS, THM, EVT, …) | ~1,040 | agents | 40–50 h of agent runtime, including ~30% for integration, coherence checks and rework |
| Migration with real data, dress rehearsals, cutover (MIG.7, MIG.9, MIG.10) | ~45 | agents and people | included in the people hours below |
| Minor review findings of waves 5 and 6 (41 items, [progress](../progress.md)) | ~20 | agents | one cleanup wave of 5 batches, about 1.5 h of wall-clock time |

That is at least two weekly budgets, so **3–4 calendar weeks for P1 code**, provided the open
decisions are answered in time. The schedule is now gated more by decisions and accounts than by
agent time: STAGE (ARC.13, which needs the Hetzner account) gates 40 P1 tasks worth 395 hours (51 and 489
before the ARC.16 split), and
the open D-xx decisions gate most of the rest (D-04 20 tasks and 239 hours, D-06 21 tasks and
173 hours, D-14 11 tasks and 124 hours, D-01 12 tasks and 106 hours). D-07 (29 tasks and 241 hours
at this measurement) was decided by the owner on the same day and no longer gates them.

Work that agents cannot speed up:

| Activity | People hours |
|---|---:|
| Owner decisions (question register and open D-xx) | 5–8 |
| Tax advisor and legal counsel (D-01, D-02, D-03 now covering the per-method shipping VAT treatment, D-08, the D-21 UK confirmation, the RF20 mapping of VEN.18, the Swiss threshold of CMP.16, double signature, the one-off review of the (L) wording with the CMP.24 approvals and the CMP.25 self-certification) | 8–14, plus 2–6 weeks of waiting |
| Human review of tax, payments, ledger and orders code | 30–50 |
| Hetzner, DNS, Stripe Connect and email provider accounts | 6–12 |
| Migration checks and dress rehearsals | 15–25 |
| Content, legal pages, semi-manual blog migration | 15–30 |
| Acceptance testing with vendors A–E | 20–40 |
| Go-live and monitoring | 10–20 |
| **Total** | **≈ 109–199** |

**Result:** about **150–250 people hours** plus about two weekly agent budgets, against
1,945–2,179 hours for a human team (§91.2). Realistic calendar to go-live: **2–3 months**. The
pace is set by external waits: legal counsel, the Hetzner account for STAGE, Stripe Connect
verification, the external penetration test (SEC.6) and the three dress rehearsals (MIG.9).

### Limits of this forecast

- The 33 tasks done so far are foundations with a very clear spec. Tax, payments and orders need
  more integration, and agent speed may halve. Even then, P1 code stays within 4–6 weeks.
- The 41 open minor review findings (see [progress](../progress.md)) are counted above as a
  cleanup wave; findings of later waves will add to it.
- People hours assume one owner working with the agents. A separate senior reviewer changes who
  does the review, not the total.
