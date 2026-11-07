# MPMV task runner. Local containers use Apple `container` (ADR-0002); no Docker, no compose.
.DEFAULT_GOAL := help
.PHONY: help install docs docs-check docs-open db db-stop db-reset db-shell migrate run test check lint fmt typecheck imports

DB_NAME      := mpmv-db
DB_IMAGE     := docker.io/library/postgres:17
DB_VOLUME    := mpmv-pgdata
DB_PORT      ?= 55432
MANAGE       := uv run python manage.py

help: ## List available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install Python deps (uv) and git hooks
	uv sync
	uv run pre-commit install

# --- Spec and backlog
docs: ## Regenerate backlog (md/csv) and the single-page HTML spec in build/
	uv run tools/build_docs.py

docs-check: ## Fail if the committed backlog is out of date with the spec tables
	uv run tools/build_docs.py --check

docs-open: docs ## Build and open the HTML spec
	open build/mpmv-spec.html

# --- Local database (Apple container)
db: ## Start local PostgreSQL in an Apple container (idempotent)
	@container volume inspect $(DB_VOLUME) >/dev/null 2>&1 || container volume create $(DB_VOLUME) >/dev/null
	@if container inspect $(DB_NAME) >/dev/null 2>&1; then \
		container start $(DB_NAME) >/dev/null 2>&1 || true; \
	else \
		container run -d --name $(DB_NAME) -p 127.0.0.1:$(DB_PORT):5432 \
			-e POSTGRES_USER=mpmv -e POSTGRES_PASSWORD=mpmv -e POSTGRES_DB=mpmv \
			-e PGDATA=/var/lib/postgresql/data/pgdata \
			-v $(DB_VOLUME):/var/lib/postgresql/data $(DB_IMAGE) >/dev/null; \
	fi
	@# Apple container volumes are ext4 with lost+found at the root, hence PGDATA in a subdirectory.
	@for i in $$(seq 60); do \
		container exec $(DB_NAME) pg_isready -U mpmv -d mpmv >/dev/null 2>&1 && exit 0; sleep 1; \
	done; echo "PostgreSQL did not become ready; see: container logs $(DB_NAME)"; exit 1
	@echo "PostgreSQL ready on 127.0.0.1:$(DB_PORT)"

db-stop: ## Stop the local database container
	-container stop $(DB_NAME)

db-reset: ## Destroy the local database container and its volume
	-container stop $(DB_NAME)
	-container delete $(DB_NAME)
	-container volume delete $(DB_VOLUME)

db-shell: ## Open psql in the local database
	container exec -it $(DB_NAME) psql -U mpmv -d mpmv

# --- Application
migrate: db ## Apply migrations to the local database
	$(MANAGE) migrate

run: migrate ## Run the development server
	$(MANAGE) runserver

# --- Quality gates
test: db ## Run the test suite (needs the local database)
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Static type check with mypy
	uv run mypy .

imports: ## Enforce the app dependency graph (spec 01 §1.3)
	uv run lint-imports

check: lint typecheck imports docs-check ## All static checks (no database needed)
	$(MANAGE) check --settings=mpmv.settings.test
