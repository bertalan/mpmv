# AI text enrichment (ING.5)

`apps/ingest/enrichment.py` defines the `TextEnricher` protocol (D-13). Providers are mocked only
through it. `apps/ingest/enrich_engine.py` holds the pure schema and policy checks.
`services.enrich_draft` runs an enricher on a `ProductDraft`.

## Rules

- English only (D-18, D-18 bis). The service refuses any language the policy does not allow, and
  any language that already has texts in the draft or in the product's catalogue translations, so
  the Italian texts migrated by MIG.3 are never written or overwritten (Q-71). The provider is not
  called in those cases.
- Every provider answer is archived in `EnrichmentVersion` (provider, model, prompt version,
  outcome, raw answer, violations, time).
- A schema-invalid or out-of-policy answer is never applied: it opens a `text_policy` review item
  and the draft goes to `needs_review`. An accepted answer is stored in `draft.enriched[lang]`
  with `origin: machine` and is not published here (CMP.8, ING.14).
- A provider error changes nothing.
- The policy blocks HTML, links, over-long fields, generic environmental claims (ECGT) and
  unverified fitment claims. Add words to `TextPolicy.banned_terms` per vendor if needed.

## Choosing the provider

| Setting (env) | Default | Meaning |
|---|---|---|
| `MPMV_INGEST_ENRICHER` | `fake` | `fake` or `gemini`. Tests always use `fake` |
| `MPMV_INGEST_GEMINI_API_KEY` | empty | Gemini key; environment only, never in code or tests |
| `MPMV_INGEST_GEMINI_MODEL` | `gemini-2.0-flash` | Model name stored in the archive |

`apps.ingest.gemini.get_enricher()` returns the chosen enricher. Claude and OpenAI, per-vendor keys
and encrypted storage are ING.14. The Gemini call uses `apps.core.outbound.open_url` (no redirects);
a TODO(MIG.12) marks the move to `safe_http` when it lands. The key is sent in a header and never
logged. Only product data is sent, never customer data.
