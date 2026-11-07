# Contributing

## Source of truth

| What | Where | Edit by hand? |
|---|---|---|
| Functional/technical spec | `docs/spec/NN-*.md` | Yes, through review |
| Micro-tasks (ID, priority, hours, deps, done-when) | Task tables in each `docs/spec/NN-*.md` | Yes |
| Backlog by phase, CSV export | `docs/spec/90-backlog.md`, `docs/spec/backlog.csv` | **No**, run `make docs` |
| Open decisions | `docs/spec/92-open-decisions.md` | Yes, and close each one with an ADR |
| Architecture decisions | `docs/adr/NNNN-*.md` | Yes, never rewrite an accepted ADR; supersede it |

Everything in the repository is in English: spec, ADRs, code, comments, docstrings, commit messages
and developer docs.

## Task lifecycle

1. **Ready**: the task's dependencies are done and the open decisions blocking it (see
   `92-open-decisions.md`, column "Blocks") are closed.
2. **In progress**: branch `<task-id>-<slug>`, e.g. `arc-3-bootstrap`, `tax-3-resolve-goods-tax`.
3. **Review**: PR titled `[ARC.3] Bootstrap repository`. The PR body links the spec section and
   restates the task's "Done when" criterion.
4. **Done**: merged into `main` with the criterion demonstrably met (test, demo on STAGE or report).

Changing scope or an estimate means editing the task row in the spec in the same PR, then running
`make docs`.

## Commits

- Imperative mood, ≤72-character subject, task ID as a prefix: `ARC.6: add MoneyField with ROUND_HALF_UP per line`.
- One logical change per commit. Generated files are committed together with the source change.

## Definition of done (every task)

- Automated tests cover the behaviour. Domain regression cases listed in the spec are mandatory.
- `make check` is green (lint, types, tests, import rules; available from ARC.3/ARC.4).
- Human line-by-line review for anything touching tax, payments, ledger, orders or security.
- No secrets in code, fixtures or docs. Config comes from environment variables only.
- Spec and ADRs are updated if behaviour or decisions changed.
