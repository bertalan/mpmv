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
| P1 micro-tasks | 1,479 | Sum from the generated backlog |
| Integrated and end-to-end acceptance testing, exploratory tests | 100 | ~10% of P1 |
| Mandatory human review of tax, payments, ledger, orders, security | 50 | ~15% of the hours for TAX, PAY, VEN, ORD, SEC |
| Coordination, ADRs, meetings with tax advisor and legal counsel | 80 | ~8% of P1 |
| **Subtotal** | **1,709** | |
| Contingency for unforeseen issues (10–25% of P1) | 148–370 | Tax and payment domain, dirty legacy data |
| **Go-live total** | **≈ 1,857–2,079** | |

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

After go-live: P2 = 284 h and P3 = 18 h (MCP, advanced zone editor, OSS report, visitor content
translation, reviews, advanced payouts, vendor branding, customer preferences and event
notifications, Google geocoder and map layer…), about **330–380 h** including contingency.

## 91.3 Comparison with the August plan

| | August plan | This review |
|---|---:|---:|
| Scope | Full WCFM parity + P1 commercial modules | Parity **with what is actually used** + 2026 compliance |
| Total hours (development, testing, migration) | 1,740–2,540 | 1,857–2,079 to go-live |
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
  checkout, emails and legal/tax wording. If the owner decides that legal pages are translated at
  go-live, counsel also checks the legal texts in each language (Impressum, terms, withdrawal notice).
- Translation of visitor content after go-live (I18N.8), with the method the owner chooses.
- CMP for cookies; transactional email provider. No SDI intermediary: the platform issues no
  invoices, and the operator's accountant invoices commissions with its own tool from the CMP.2
  statement.
- Hosting: Hetzner Cloud VPS (app and database per environment), Hetzner Object Storage for media and backups, CDN, error tracking.
- Custom graphics and UX beyond the `base` theme and the first vertical theme (spec 14), such as a
  logo, illustrations or commissioned photography.

In exchange, the recurring costs of premium WordPress licenses disappear (WCFM Ultimate, Membership,
Affiliate, Groups & Staffs, Analytics, WPML, WPSSO Premium and add-ons) and, above all, so do the
maintenance hours for updates, incompatibilities and fixes (STAGE, test matrices, option patches).

## 91.6 Measured velocity with agents (non-binding)

This section is a forecast, not a commitment. It does not replace the hours above, which stay the
reference for a human team. It is refreshed at every coherence check.

### Measurement of 2026-11-07

Source: merged pull requests and their timestamps, the generated backlog, and the plan usage meter.

- **Done:** 30 P1 tasks, worth **225 estimated hours**. They are waves 0–5: ARC, CORE, VEN.1/5/12,
  SEC.1/3/4, I18N.1/2, SHP.1, CAT.1, TAX.1 and CMS.1.
- **Throughput:** one wave of 5 tasks is worth 36–44 estimated hours. Implementation, independent
  review, fixes, CI and merge take **80–95 minutes** of wall-clock time, about 25–30 times faster
  than the estimate.
- **Real limit:** the AI plan's weekly budget, not speed. At 35% of the weekly budget (spec writing
  included) with work stopping at 80%, one week covers about **500 estimated hours** of agent work.

### Forecast for the rest of P1

97 P1 tasks remained, worth 947 estimated hours. The theme rows added the same day (THM.1 to
THM.10 and CMP.9, replacing CMS.7) bring this to 107 tasks and 996 hours, the regulatory check
rows (CMP.5 moved to P1, CMP.8, CMP.10 to CMP.13, VEN.18) to 114 tasks and 1,064 hours, the
owner's theme answers (CMS.8 and CMS.9) to 116 tasks and 1,073 hours, and the D-21 answers
(CMP.11 grows, CMP.14, CMP.15 and CMP.24 added) to 119 tasks and 1,097 hours, and the events
module (EVT.1 to EVT.15) to 134 tasks and 1,210 hours, and the owner's events answers (VEN.20
added, EVT.7 smaller) to 135 tasks and 1,220 hours.

| Part | Estimated hours | Done by | Forecast |
|---|---:|---|---|
| Code and tests (CAT, ORD, SHP, TAX, PAY, VEN, CMP, ING, SEO, CMS, THM, EVT, …) | ~1,010 | agents | 40–50 h of agent runtime, including ~30% for integration, coherence checks and rework |
| Migration with real data, dress rehearsals, cutover (MIG.7, MIG.9, MIG.10) | ~45 | agents and people | included in the people hours below |

That is at least two weekly budgets, so **3–4 calendar weeks for P1 code**, provided the open
decisions are answered in time.

Work that agents cannot speed up:

| Activity | People hours |
|---|---:|
| Owner decisions (question register and open D-xx) | 5–8 |
| Tax advisor and legal counsel (D-01, D-02, D-03, D-08, the D-21 UK confirmation, double signature, CMP.24 approvals) | 5–10, plus 2–6 weeks of waiting |
| Human review of tax, payments, ledger and orders code | 30–50 |
| Hetzner, DNS, Stripe Connect and email provider accounts | 6–12 |
| Migration checks and dress rehearsals | 15–25 |
| Content, legal pages, semi-manual blog migration | 15–30 |
| Acceptance testing with vendors A–E | 20–40 |
| Go-live and monitoring | 10–20 |
| **Total** | **≈ 105–195** |

**Result:** about **150–250 people hours** plus about two weekly agent budgets, against
1,857–2,079 hours for a human team (§91.2). Realistic calendar to go-live: **2–3 months**. The
pace is set by external waits: legal counsel, Stripe Connect verification, the external
penetration test (SEC.6) and the three dress rehearsals (MIG.9).

### Limits of this forecast

- The 30 tasks done so far are foundations with a very clear spec. Tax, payments and orders need
  more integration, and agent speed may halve. Even then, P1 code stays within 4–6 weeks.
- Minor review findings are still open (see [progress](../progress.md)). They are rework that the
  measurement does not yet include.
- People hours assume one owner working with the agents. A separate senior reviewer changes who
  does the review, not the total.
