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
| `MPMV_INGEST_ENRICHER` | `fake` | `fake`, `gemini`, `claude` or `openai`. Tests always use `fake` |
| `MPMV_INGEST_GEMINI_API_KEY` | empty | Gemini key; environment only, never in code or tests |
| `MPMV_INGEST_GEMINI_MODEL` | `gemini-2.0-flash` | Model name stored in the archive |
| `MPMV_INGEST_CLAUDE_API_KEY`, `MPMV_INGEST_OPENAI_API_KEY` | empty | Same, for the other two providers (environment only) |
| `MPMV_INGEST_CLAUDE_MODEL`, `MPMV_INGEST_OPENAI_MODEL` | `claude-haiku-4-5`, `gpt-4o-mini` | Model names |

`apps.ingest.gemini.get_enricher()` returns the chosen enricher. Per vendor (ING.14),
`apps.ingest.provider_services.resolve_enricher(vendor)` picks the vendor's own provider and key;
a vendor with no provider of its own gets the platform default (`settings.INGEST_ENRICHER`, else
fake). The vendor key is an `EncryptedCharField` with its own keyring (SEC.3, `MPMV_AI_KEY_ENCRYPTION_KEYS`,
`rotate_field_keys --keyring ai`). A key that cannot be read raises `ProviderError`; nothing falls
back to another provider. `usage_count(vendor)` counts archived answers. The Gemini call uses
`apps.core.outbound.open_url` (no redirects); `manage check` fails (`ingest.E001`) when a provider
is selected without a key. The adapters retry with pauses and sleep in the calling thread, so
`enrich_draft` with a real provider runs **only in a background job**, never in a request. A
TODO(MIG.12) marks the move to `safe_http` when it lands. The key is sent in a header and never
logged. Only product data is sent, never customer data.

## Hardening (peer and lane-a reviews)

- Markup is never accepted (no sanitiser backs it): tags, comments, entity-encoded tags, `javascript:`
  URLs and markdown links go to review. No template renders enriched text with `|safe`
  (checked by grep); keep it that way.
- Schema: every key must be present and below its hard ceiling (`HARD_MAX_LENGTHS`), whatever the policy.
- The environmental, fitment and boilerplate lists match whole words, after NFKC and case folding.
  Harmless sentences such as "compatible with dishwashers" still go to review: the safe side.
- Gemini: the transport maps every failure (HTTP, `http.client`, socket, timeout) to `ProviderError`,
  reads at most 1 MB, and retries 429 and 5xx twice with a pause. The supplier text goes into the
  prompt as one JSON block marked as data; the schema and policy checks are the real gate.
  `get_enricher()` raises `ProviderError` when `gemini` has no key: callers must handle it.
