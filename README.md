# MPMV (Marketplace Multi Vendor)

[![CI](https://github.com/bertalan/mpmv/actions/workflows/ci.yml/badge.svg)](https://github.com/bertalan/mpmv/actions/workflows/ci.yml)

A vertical-agnostic multivendor marketplace built as a Django + Wagtail modular monolith on
PostgreSQL. It replaces a WordPress + WooCommerce + WCFM + WPML stack.

> Status: **foundations (phase F0)**, with the first models of later phases (vendors, catalog,
> tax, shipping, CMS) merged. The stack is decided ([ADR-0001](docs/adr/0001-stack.md)).
> Task status and coherence checks: [docs/progress.md](docs/progress.md).

## Repository layout

```
mpmv/          Django project: settings per environment (base/dev/test/stage/live), urls, asgi/wsgi
apps/          One Django app per bounded context (core, accounts, vendors, catalog, tax, shipping,
               cart, orders, payments, ledger, ingest, cms, events, themes, seo, compliance, api,
               mcp), plus seed (demo data, ARC.10)
themes/        Frontend themes, one directory per theme (THM.1, docs/dev/themes.md)
tests/         Project-level tests: unit/, integration/, e2e/ (ADR-0006)
docs/
  spec/        Functional and technical specification; README.md is the entry point,
               90-backlog.md and backlog.csv are GENERATED
  adr/         Architecture Decision Records, one file per decision
  dev/         Developer guides (background jobs, audit trail, email, themes, the Manager, parallel lanes...)
tools/
  build_docs.py  Builds the backlog and the HTML spec, validates task IDs/deps/cycles
build/         Generated output, not versioned (HTML spec)
```

The app layout and the import rules between apps are described in
[spec 01 §1.3](docs/spec/01-stack-and-architecture.md) and enforced by import-linter.

## Getting started

Requirements: macOS with Apple [`container`](https://github.com/apple/container) (local containers,
[ADR-0002](docs/adr/0002-local-containers-apple.md)) and [uv](https://docs.astral.sh/uv/).

```sh
make install     # Python deps, Playwright browser, git hooks
make db          # PostgreSQL 17 in an Apple container on 127.0.0.1:55432
make run         # migrate + development server
```

## Common commands

```sh
make test-unit   # unit suite: pure functions, no database
make test        # unit + integration suites (starts the local database)
make test-e2e    # Playwright e2e suite, screenshots in build/e2e/screenshots/
make check       # ruff, mypy, import contracts, backlog freshness, Django system checks
make fmt         # auto-fix and format
make docs        # regenerate backlog + build/mpmv-spec.html
make docs-open   # build and open the HTML spec
make db-reset    # destroy the local database container and volume
```

## Working on the project

See [CONTRIBUTING.md](CONTRIBUTING.md) for the task lifecycle, branch/commit conventions and the
definition of done, including the TDD workflow. Editor setup: [docs/dev/vscode.md](docs/dev/vscode.md).
AI agents also read [CLAUDE.md](CLAUDE.md).

The legacy system is a separate private repository and a **read-only reference**.
