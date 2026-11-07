# piac

Rebuild of a multivendor marketplace (today: WordPress + WooCommerce +
WCFM + WPML) as a Django + Wagtail modular monolith on PostgreSQL.

> Status: **specification under review**. No application code yet. Bootstrapping starts with
> backlog task ARC.3 once [ADR-0001](docs/adr/0001-stack.md) is accepted.

## Repository layout

```
docs/
  spec/     Functional and technical specification (Italian), approved section by section
            README.md is the entry point; 90-backlog.md and backlog.csv are GENERATED
  adr/      Architecture Decision Records (English), one file per decision
tools/
  build_docs.py   Builds the backlog and the single-page HTML spec, validates task IDs/deps/cycles
build/      Generated output, not versioned (HTML spec)
```

Application code (`piac/`, `apps/`, `Containerfile`, `pyproject.toml`) is added by ARC.3 and ARC.5,
following the layout in [spec 01 §1.3](docs/spec/01-stack-e-architettura.md).

## Common commands

Requirements: [uv](https://docs.astral.sh/uv/), and Apple [`container`](https://github.com/apple/container) for local containers.

```sh
make docs        # regenerate backlog + build/piac-specifica.html
make docs-check  # fail if the committed backlog is stale (use in CI / pre-commit)
make docs-open   # build and open the HTML spec
```

## Working on the project

See [CONTRIBUTING.md](CONTRIBUTING.md) for the task lifecycle, branch/commit conventions and the
definition of done. AI agents also read [CLAUDE.md](CLAUDE.md).

The legacy system is a separate private repository and a **read-only reference**.
