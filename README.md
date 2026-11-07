# MPMV (Marketplace Multi Vendor)

A vertical-agnostic multivendor marketplace built as a Django + Wagtail modular monolith on
PostgreSQL. It replaces a WordPress + WooCommerce + WCFM + WPML stack.

> Status: **foundations (phase F0)**. The stack is decided ([ADR-0001](docs/adr/0001-stack.md)) and
> the project skeleton is in place (ARC.3). Domain apps are empty shells until their tasks start.

## Repository layout

```
mpmv/          Django project: settings per environment (base/dev/test/stage/live), urls, asgi/wsgi
apps/          One Django app per bounded context (core, accounts, vendors, catalog, tax, shipping,
               cart, orders, payments, ledger, ingest, cms, seo, compliance, api, mcp)
tests/         Project-level tests
docs/
  spec/        Functional and technical specification; README.md is the entry point,
               90-backlog.md and backlog.csv are GENERATED
  adr/         Architecture Decision Records, one file per decision
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
make install     # Python deps + git hooks
make db          # PostgreSQL 17 in an Apple container on 127.0.0.1:55432
make run         # migrate + development server
```

## Common commands

```sh
make test        # pytest (starts the local database if needed)
make check       # ruff, mypy, import contracts, backlog freshness, Django system checks
make fmt         # auto-fix and format
make docs        # regenerate backlog + build/mpmv-spec.html
make docs-open   # build and open the HTML spec
make db-reset    # destroy the local database container and volume
```

## Working on the project

See [CONTRIBUTING.md](CONTRIBUTING.md) for the task lifecycle, branch/commit conventions and the
definition of done. AI agents also read [CLAUDE.md](CLAUDE.md).

The legacy system is a separate private repository and a **read-only reference**.
