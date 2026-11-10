# DSA and P2B duties (CMP.5)

Part 1 of CMP.5 covers the points of contact, the platform terms and the ranking page.

- **Points of contact** (DSA articles 11 and 12) are fields of `PlatformOperator`: the authorities'
  email and languages, the support email and an optional https contact form. Change them through
  `configure_platform_operator` (audited). They are shown at `/contact-points/`. Nothing links to
  that page yet: the footer or legal menu link comes with a later part.
- **Required sections.** The legal page kinds `platform_terms` and `ranking` carry
  `legal_section` blocks, tagged `restrictions`, `moderation_policy` or `ranking_parameters`.
  Editors write the wording; code only checks the tags (`apps.compliance.engine.REQUIRED_SECTIONS`).
- **Readiness.** `manage.py dsa_p2b_readiness [--language xx]` exits 1 and lists what is missing:
  contact data, or a live page or section per language (no EN fallback). It is separate from
  `golive_readiness` for now (open owner question: whether it joins it, with the I18N.9 reviews).
