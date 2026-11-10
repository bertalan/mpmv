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

## Test-driven development

Every task follows red → green → refactor ([ADR-0006](docs/adr/0006-testing-strategy.md)):

1. Turn the task's "Done when" criterion and the spec's regression cases into failing tests.
   The first commit on the branch contains only those tests: `test(TASK.ID): …`.
2. Write the minimum code that makes them pass.
3. Refactor while the tests stay green.

Tests live in three suites, chosen by directory: `tests/unit` and `apps/<app>/tests/unit` (pure
functions, no database), `.../tests/integration` (real Django + PostgreSQL, no mocks), and
`tests/e2e` (Playwright on a live server, with screenshots). Mock only external boundaries, and
only through our own interfaces. Editor setup: [docs/dev/vscode.md](docs/dev/vscode.md).

## Test data

Tests build their data with [factory_boy](https://factoryboy.readthedocs.io/en/stable/). Each app
keeps one `DjangoModelFactory` per model in `apps/<app>/tests/factories.py`; a new model needs a
factory, and a test enforces it. Use `.build()` in unit tests (no database) and `.create()`
elsewhere, and pass explicitly the values a test asserts on. Faker runs with a fixed seed that is
reset before every test (`MPMV_TEST_SEED` changes it), so failures are reproducible. Data is
synthetic only: pseudonyms like "Vendor A", `example.com` addresses, never real people or
vendors. No credential-like literals: generate passwords and keys at runtime with
`secrets.token_urlsafe()`. Details in [ADR-0006](docs/adr/0006-testing-strategy.md).

For parallel work (worktrees, agents), set `MPMV_TEST_DB_NAME=test_mpmv_<branch>` so test databases do not collide on the shared local PostgreSQL ([parallel-lanes.md](docs/dev/parallel-lanes.md)).

## Commits

- Imperative mood, ≤72-character subject, task ID as a prefix: `ARC.6: add MoneyField with ROUND_HALF_UP per line`.
- One logical change per commit. Generated files are committed together with the source change.

## Definition of done (every task)

- Tests were written first (TDD) and cover the behaviour. Domain regression cases listed in the spec are mandatory.
- `make check` and `make test` are green; `make test-e2e` too when the task touches a user journey.
- Human line-by-line review for anything touching tax, payments, ledger, orders or security.
- No secrets in code, fixtures or docs. Config comes from environment variables only.
- Spec and ADRs are updated if behaviour or decisions changed.

## Parallel tests

`make test` runs the unit and integration suites with pytest-xdist (`--dist loadgroup`; tests that flush the
database run last, one group per test, see `conftest.py`). pytest-django gives every worker its own database, named
`$MPMV_TEST_DB_NAME` plus `_gw0`, `_gw1`, and so on. Locally the default is 2 workers, which is safe
on a low-RAM machine; use `make test MPMV_TEST_WORKERS=4` to raise it, or `0` to run serially. CI
uses `-n auto`. Several worktrees can still share one PostgreSQL: set `MPMV_TEST_DB_NAME` per branch,
and run the full suite under the `flock` rule. Tests must not depend on order or on state shared
between files (module globals, fixed ports, fixed file names): use `tmp_path` and per-test data.

## Releases

Tag only a green `main` commit by its full SHA; STAGE first, LIVE on the owner's go, no hand edits
on servers. The rules are in [docs/dev/release.md](docs/dev/release.md).
