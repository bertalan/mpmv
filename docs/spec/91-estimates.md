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
| P1 micro-tasks | 1,172 | Sum from the generated backlog |
| Integrated and end-to-end acceptance testing, exploratory tests | 100 | ~10% of P1 |
| Mandatory human review of tax, payments, ledger, orders, security | 50 | ~15% of the hours for TAX, PAY, VEN, ORD, SEC |
| Coordination, ADRs, meetings with tax advisor and legal counsel | 80 | ~8% of P1 |
| **Subtotal** | **1,402** | |
| Contingency for unforeseen issues (10–25% of P1) | 117–293 | Tax and payment domain, dirty legacy data |
| **Go-live total** | **≈ 1,520–1,700** | |

After go-live: P2 = 185 h and P3 = 18 h (MCP, advanced zone editor, OSS report, visitor content
translation, reviews, advanced payouts…), about **220–260 h** including contingency.

## 91.3 Comparison with the August plan

| | August plan | This review |
|---|---:|---:|
| Scope | Full WCFM parity + P1 commercial modules | Parity **with what is actually used** + 2026 compliance |
| Total hours (development, testing, migration) | 1,740–2,540 | 1,520–1,700 to go-live |
| With AI-assisted development | 1,450–2,100 | already included |

Where the difference comes from:

- **Fewer components**: no Celery/Redis/Elasticsearch and no full vendor dashboard.
- **Modules not migrated**: membership with billing, affiliate, staff, analytics.
- **Reuse of the pipelines** as Python code in the same project: no HTTP client towards PHP,
  no STAGE → LIVE promotion.
- **Agentic development** used systematically on the mechanical parts (models, CRUD, admin, parametric
  tests). The tax and payment parts remain under line-by-line human review.

Additions compared with August: online withdrawal, EAA/accessibility, operational GPSR, MCP server.

## 91.4 Indicative schedule

The P1 critical path is short (~126 h: foundations → catalog → orders → payments → refunds):
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
| F1 Catalog and import | Catalog, multilingual, pipelines for Vendor A and the other vendors, theme | LIVE-equivalent catalog browsable on STAGE, fed by the pipelines |
| F2 Checkout | Tax, shipping, quotes, cart, orders, Stripe | Multi-vendor order paid in test mode with correct transfers |
| F3 Vendors and compliance | Ledger, payouts, vendor dashboard, DAC7, GPSR, GDPR, withdrawal | Pilot vendor operates on their own on STAGE |
| F4 Content and SEO | Wagtail pages, blog, taxonomy landing pages, JSON-LD, feeds | Rich Results and Merchant feeds validated |
| F5 Go-live | Migration, parity, dress rehearsals, pentest, cutover | Third dress rehearsal with no blockers → go-live |

A milestone is closed only with a working demo on STAGE and the domain regression tests green.

## 91.5 External costs to be quoted separately

- External penetration test (SEC.6; the hours listed are for remediation).
- Tax advisor and legal counsel for the ⚖️ decisions in [92-open-decisions.md](92-open-decisions.md).
- Interface text in IT, DE, FR and ES at go-live (D-18): native-speaker review of the AI drafts for
  checkout, emails and legal/tax wording. If the owner decides that legal pages are translated at
  go-live, counsel also checks the legal texts in each language (Impressum, terms, withdrawal notice).
- Translation of visitor content after go-live (I18N.8), with the method the owner chooses.
- SDI intermediary for commission invoices; CMP for cookies; transactional email provider.
- Hosting: Hetzner Cloud VPS (app and database per environment), Hetzner Object Storage for media and backups, CDN, error tracking.
- Custom graphics and UX, if you want to go beyond a clean, accessible theme.

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

97 P1 tasks remain, worth 947 estimated hours.

| Part | Estimated hours | Done by | Forecast |
|---|---:|---|---|
| Code and tests (CAT, ORD, SHP, TAX, PAY, VEN, CMP, ING, SEO, CMS, …) | ~850 | agents | 40–50 h of agent runtime, including ~30% for integration, coherence checks and rework |
| Migration with real data, dress rehearsals, cutover (MIG.7, MIG.9, MIG.10) | ~45 | agents and people | included in the people hours below |

That is at least two weekly budgets, so **3–4 calendar weeks for P1 code**, provided the open
decisions are answered in time.

Work that agents cannot speed up:

| Activity | People hours |
|---|---:|
| Owner decisions (question register and open D-xx) | 5–8 |
| Tax advisor and legal counsel (D-01, D-02, D-03, D-08, double signature) | 5–10, plus 2–6 weeks of waiting |
| Human review of tax, payments, ledger and orders code | 30–50 |
| Hetzner, DNS, Stripe Connect and email provider accounts | 6–12 |
| Migration checks and dress rehearsals | 15–25 |
| Content, legal pages, semi-manual blog migration | 15–30 |
| Acceptance testing with vendors A–E | 20–40 |
| Go-live and monitoring | 10–20 |
| **Total** | **≈ 105–195** |

**Result:** about **150–250 people hours** plus about two weekly agent budgets, against
1,520–1,700 hours for a human team (§91.2). Realistic calendar to go-live: **2–3 months**. The
pace is set by external waits: legal counsel, Stripe Connect verification, the external
penetration test (SEC.6) and the three dress rehearsals (MIG.9).

### Limits of this forecast

- The 30 tasks done so far are foundations with a very clear spec. Tax, payments and orders need
  more integration, and agent speed may halve. Even then, P1 code stays within 4–6 weeks.
- Minor review findings are still open (see [progress](../progress.md)). They are rework that the
  measurement does not yet include.
- People hours assume one owner working with the agents. A separate senior reviewer changes who
  does the review, not the total.
