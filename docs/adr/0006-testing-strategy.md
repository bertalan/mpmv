# ADR-0006: Test-driven development with three test suites

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.11, ARC.4, ARC.12

## Context

The project owner asked for TDD, with test data built by factory_boy
(https://factoryboy.readthedocs.io/en/stable/). The strategy rests on a few principles:

- Test against the real runtime, not mocks: the real Django stack on a real PostgreSQL.
- Separate suites for separate concerns: pure logic, the integrated application, and user
  journeys in a browser.
- Bootstrap only what a test needs: pure engines run with no database at all.
- Explicit assertions: tests check concrete values and finish in a known state.
- Diagnosable failures: reports are kept when a build fails, and e2e screenshots are always kept.
- Reproducible data: every test builds its own synthetic data from factories with a fixed seed.

## Decision

### Workflow (TDD)

Every task follows red → green → refactor:

1. Turn the task's "Done when" criterion and the spec's regression cases into failing tests.
   The first commit on the task branch contains only these tests (`test(TASK.ID): …`).
2. Write the minimum code that makes them pass.
3. Refactor with the tests green. `make check` and `make test` must be green before the PR.

### Three suites

| Suite | What it covers | Database | Location | Marker |
|---|---|---|---|---|
| **unit** | Pure functions (`engine.py`), value objects, validators | **Forbidden** | `apps/<app>/tests/unit/`, `tests/unit/` | `unit` |
| **integration** | Services, models, constraints, views and API through the Django test client, real PostgreSQL | Real PostgreSQL | `apps/<app>/tests/integration/`, `tests/integration/` | `integration` |
| **e2e** | User journeys in a real browser (Playwright) against a live server; a screenshot at every key step | Real PostgreSQL | `tests/e2e/` | `e2e` |

- The directory decides the marker. A root `conftest.py` assigns it, so no test is left unclassified.
- Unit tests that touch the database fail: pytest-django blocks access without `django_db`, and
  the unit suite never uses that marker.
- Test files mirror the module under test: `apps/tax/engine.py` → `apps/tax/tests/unit/test_engine.py`.
- Mocks are allowed only at external boundaries (Stripe, VIES, email provider, supplier sites),
  and only through the interfaces we own (ADR-0003). Recorded fixtures are preferred to hand-written mocks.

### Test data (ARC.12)

- Tests build their data with factory_boy: one `DjangoModelFactory` per model, in
  `apps/<app>/tests/factories.py`. A test in `tests/unit/test_factories.py` fails when a model
  has no factory or a factory is not used by any test.
- Unit tests use `.build()`, which never touches the database; integration and e2e tests use
  `.create()`. Tests pass the values they assert on explicitly and leave the rest to the factory.
- Faker and factory_boy are reseeded with a fixed seed before every test, and factory sequences
  restart, so a failing test replays with the same data whatever ran before it. The seed is
  printed in the pytest header and can be changed with `MPMV_TEST_SEED`.
- Data is synthetic: pseudonyms such as "Vendor A", `example.com` addresses, Faker text. Never
  real vendors, customers or LIVE dumps. The synthetic seed for local and STAGE data (ARC.10)
  is expected to reuse these factories.
- No credential-like literals: passwords and API keys are generated at runtime with
  `secrets.token_urlsafe()` or the app's own key generator.
- pytest fixtures wrap factories only when they save real repetition across apps, such as the
  `superuser` fixture in the root `conftest.py`. pytest-factoryboy is not used: explicit
  factory calls keep each test's data visible in the test.

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
