# DSA and P2B duties (CMP.5)

Part 1 of CMP.5 covers the points of contact, the platform terms and the ranking page.

- **Points of contact** (DSA articles 11 and 12) are fields of `PlatformOperator`: the authorities'
  email and languages, the support email and an optional https contact form. Change them through
  `configure_platform_operator` (audited). They are shown at `/contact-points/`. Nothing links to
  that page yet: the footer or legal menu link comes with a later part.
- **Required sections.** The legal page kinds `platform_terms` and `ranking` carry
  `legal_section` blocks. The terms need `restrictions`, `moderation_policy`, `complaint_handling`
  (article 20) and `redress` (article 21); the ranking page needs `ranking_parameters`.
  Editors write the wording; code only checks the tags (`apps.compliance.engine.REQUIRED_SECTIONS`).
- **Readiness (D-27).** `manage.py golive_readiness` is the one go-live gate: it checks the
  platform terms and ranking pages in every configured language with the native and counsel
  reviews (I18N.9), and runs `compliance.services.dsa_p2b_readiness` (registered through
  `apps.cms.services.register_golive_check`): contact data (an official language plus English for
  authorities) and the required sections. `manage.py dsa_p2b_readiness [--language xx]` runs only
  that sub-check. The contact page answers 404 until some contact is configured.
