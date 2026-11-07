# CLAUDE.md

Guidance for AI agents working in this repository. Read [README.md](README.md) and
[CONTRIBUTING.md](CONTRIBUTING.md) first.

## Language

- Talk to the project owner in **Italian** (chat only).
- Write everything in the repository in **English**: spec, ADRs, docs, code, comments, commit messages.

## Ground rules

- The legacy WordPress system (separate private repository; its local path is in the git-ignored
  `CLAUDE.local.md`) is a **read-only reference**. Never modify it. Never open
  `.env`, `.env.secrets` or similar files there. Never copy or print credentials, even ones found
  in clear text in legacy files; mention only the file and the fact that a secret is there.
- **The repository is public.** Never put real vendor names, legacy order numbers, hostnames,
  DB names, credentials or security weaknesses of the live legacy system in versioned files. Use
  the pseudonyms (vendors A–E, cases like FEE-NETTO); the map and the full spec live in `private/`
  (git-ignored).
- Containers: use Apple `container` (Containerization framework), not Docker or OrbStack
  ([ADR-0002](docs/adr/0002-local-containers-apple.md)).
- Every change maps to a backlog task ID (e.g. `TAX.3`). If no task covers it, propose a new row
  in the relevant spec table instead of doing unplanned work.
- Never hand-edit `docs/spec/90-backlog.md` or `docs/spec/backlog.csv`. Run `make docs`.
- Do not start a task whose blocking decision (D-xx in `docs/spec/92-open-decisions.md`) is
  still open. Ask instead.

## Architecture rules (from spec 01)

- Modular monolith: one Django project (`mpmv/`) and one app per bounded context under `apps/`.
- Business logic lives in `services.py` (I/O, ORM) and `engine.py` (pure functions, no ORM).
  Views, API, management commands and MCP are thin adapters.
- Respect the app dependency graph (spec 01 §1.3). `tax` and `shipping` never import `orders`.
- Money: `Decimal`, `MoneyField` = `DecimalField(12,2)`, `ROUND_HALF_UP` per line, never floats.
- Mutating services accept `dry_run=True` and return a `ChangeSet`. Sensitive mutations write `AuditLog`.
- Orders store snapshots (prices, tax, commission rule), never live references.
- Tax, payments, ledger and order code needs regression tests from the spec, and the PR must
  flag it for human review.

## Commands

```sh
make db           # local PostgreSQL (Apple container, port 55432)
make test         # pytest
make check        # ruff + mypy --strict + import contracts + backlog freshness + Django checks
make fmt          # auto-fix and format
make docs         # regenerate backlog + HTML spec after editing spec task tables
```

Run `make check` and `make test` before declaring a task done.
