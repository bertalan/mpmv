# ADR-0006: Test-driven development with three test suites

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.11, ARC.4

## Context

The project owner asked for TDD, modelled on the Fabric (Minecraft modding) approach to automatic
testing (https://docs.fabricmc.net/develop/automatic-testing). Its principles carry over to a
Django marketplace:

- **Test against the real runtime, not mocks.** Fabric loads tests through its own runtime and
  starts a real client and server for game tests. For us, that means the real Django stack on a
  real PostgreSQL.
- **Separate suites for separate concerns.** Fabric splits unit tests from server game tests and
  client game tests, and gives game tests their own source set.
- **Bootstrap only what a test needs.** Fabric bootstraps just enough of the game for registries.
  Our pure engines run with no database at all.
- **Explicit assertions.** Tests check concrete values and finish in a known state.
- **Diagnosable failures.** Fabric uploads reports when a build fails and always uploads
  client-test screenshots.

## Decision

### Workflow (TDD)

Every task follows red → green → refactor:

1. Turn the task's "Done when" criterion and the spec's regression cases into failing tests.
   The first commit on the task branch contains only these tests (`test(TASK.ID): …`).
2. Write the minimum code that makes them pass.
3. Refactor with the tests green. `make check` and `make test` must be green before the PR.

### Three suites

| Suite | Fabric analogue | What it covers | Database | Location | Marker |
|---|---|---|---|---|---|
| **unit** | Fabric Loader JUnit | Pure functions (`engine.py`), value objects, validators | **Forbidden** | `apps/<app>/tests/unit/`, `tests/unit/` | `unit` |
| **integration** | Server game tests | Services, models, constraints, views and API through the Django test client, real PostgreSQL | Real PostgreSQL | `apps/<app>/tests/integration/`, `tests/integration/` | `integration` |
| **e2e** | Client game tests | User journeys in a real browser (Playwright) against a live server; a screenshot at every key step | Real PostgreSQL | `tests/e2e/` | `e2e` |

- The directory decides the marker. A root `conftest.py` assigns it, so no test is left unclassified.
- Unit tests that touch the database fail: pytest-django blocks access without `django_db`, and
  the unit suite never uses that marker.
- Test files mirror the module under test: `apps/tax/engine.py` → `apps/tax/tests/unit/test_engine.py`.
- Shared test data comes from factories and the synthetic seed (ARC.10), never from LIVE dumps.
- Mocks are allowed only at external boundaries (Stripe, VIES, email provider, supplier sites),
  and only through the interfaces we own (ADR-0003). Recorded fixtures are preferred to hand-written mocks.

### Commands

| Command | Runs |
|---|---|
| `make test-unit` | Unit suite only; no database or container needed |
| `make test` | Unit + integration (starts the local database) |
| `make test-e2e` | E2E suite with Playwright; screenshots in `build/e2e/screenshots/` |
| `make test-all` | All three suites |

### CI (GitHub Actions)

- Job **tests**: unit and integration against a PostgreSQL service container, with JUnit XML and
  a coverage report. When the job fails, `build/reports/` is uploaded as an artifact.
- Job **e2e**: Playwright with headless Chromium. Screenshots are **always** uploaded, and reports
  are uploaded on failure.
- Both jobs are required to merge.

## Consequences

- Every task's PR shows its tests first, so a reviewer can check the specified behaviour before
  reading the implementation.
- The suites stay fast because pure engines need no setup. Integration tests pay for the real
  database, and that is the point.
- The e2e suite is the slowest; it covers user journeys, not every branch of the logic.
