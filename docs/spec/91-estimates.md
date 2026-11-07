# 91 — Estimates and release plan

## 91.1 How to read the hours

- Micro-task hours (see [90-backlog.md](90-backlog.md)) cover **development and automated
  tests**. They assume a senior developer who knows Django and works with an agentic AI
  assistant (Claude Code or similar).
- They do not include cross-cutting costs, which are added below.
- They are top-down estimates per micro-task, not a quote: they must be reviewed after phase F0, when
  the project skeleton exists and the actual velocity can be measured.

## 91.2 Go-live total (P1 only)

| Item | Hours | How it is calculated |
|---|---:|---|
| P1 micro-tasks | 1,045 | Sum from the generated backlog |
| Integrated and end-to-end acceptance testing, exploratory tests | 100 | ~10% of P1 |
| Mandatory human review of tax, payments, ledger, orders, security | 50 | ~15% of the hours for TAX, PAY, VEN, ORD, SEC |
| Coordination, ADRs, meetings with tax advisor and legal counsel | 80 | ~8% of P1 |
| **Subtotal** | **1,275** | |
| Contingency for unforeseen issues (10–25% of P1) | 100–255 | Tax and payment domain, dirty legacy data |
| **Go-live total** | **≈ 1,350–1,510** | |

After go-live: P2 = 189 h and P3 = 18 h (MCP, advanced zone editor, OSS report, translation queue,
reviews, advanced payouts…), about **210–250 h** including contingency.

## 91.3 Comparison with the August plan

| | August plan | This review |
|---|---:|---:|
| Scope | Full WCFM parity + P1 commercial modules | Parity **with what is actually used** + 2026 compliance |
| Total hours (development, testing, migration) | 1,740–2,540 | 1,350–1,510 to go-live |
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
- German at go-live (D-18): native-speaker review of the AI drafts for legal pages, checkout and
  emails, and German legal texts checked by counsel (Impressum, terms, withdrawal notice).
- SDI intermediary for commission invoices; CMP for cookies; transactional email provider.
- Hosting: EU VPS, managed Postgres, object storage and CDN, error tracking.
- Custom graphics and UX, if you want to go beyond a clean, accessible theme.

In exchange, the recurring costs of premium WordPress licenses disappear (WCFM Ultimate, Membership,
Affiliate, Groups & Staffs, Analytics, WPML, WPSSO Premium and add-ons) and, above all, so do the
maintenance hours for updates, incompatibilities and fixes (STAGE, test matrices, option patches).
