# MPMV task runner. Local containers use Apple `container` (ADR-0002); no Docker, no compose.
.DEFAULT_GOAL := help
.PHONY: help install docs docs-check docs-open db db-stop db-reset db-shell image dev dev-down dev-reset dev-status dev-logs migrate seed run assets worker test test-unit test-security-nightly test-e2e e2e-baselines test-all check lint fmt typecheck imports theme-gates audit sbom

DB_NAME      := mpmv-db
DB_IMAGE     := docker.io/library/postgres:17
DB_VOLUME    := mpmv-pgdata
DB_PORT      ?= 55432
MANAGE       := uv run python manage.py

help: ## List available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install Python deps (uv), the Playwright browser and git hooks
	uv sync
	uv run playwright install chromium
	uv run pre-commit install

# --- Spec and backlog
docs: ## Regenerate backlog (md/csv) and the single-page HTML spec in build/
	uv run tools/build_docs.py

docs-check: ## Fail if the committed backlog is out of date with the spec tables
	uv run tools/build_docs.py --check

docs-open: docs ## Build and open the HTML spec
	open build/mpmv-spec.html

# --- Local database (Apple container). Trust auth, bound to 127.0.0.1 only: no credentials in git.
db: ## Start local PostgreSQL in an Apple container (idempotent)
	@container volume inspect $(DB_VOLUME) >/dev/null 2>&1 || container volume create $(DB_VOLUME) >/dev/null
	@if container inspect $(DB_NAME) >/dev/null 2>&1; then \
		container start $(DB_NAME) >/dev/null 2>&1 || true; \
	else \
		container run -d --name $(DB_NAME) -p 127.0.0.1:$(DB_PORT):5432 \
			-e POSTGRES_USER=mpmv -e POSTGRES_DB=mpmv -e POSTGRES_HOST_AUTH_METHOD=trust \
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

# --- Local stack (ARC.5): the OCI image plus network, media volume, app and worker on `container`.
# Release images are built by CI from git tags; see deploy/README.md for tags and rollback.
IMAGE_NAME := mpmv-app
VERSION    ?= dev
STACK      := sh deploy/local/stack.sh

image: ## Build the app image $(IMAGE_NAME):$(VERSION) from the Containerfile
	container build --file Containerfile --tag $(IMAGE_NAME):$(VERSION) \
		--build-arg MPMV_VERSION=$(VERSION) .

dev: db image ## Run the local stack: PostgreSQL, app on http://127.0.0.1:8000, worker
	MPMV_IMAGE=$(IMAGE_NAME):$(VERSION) $(STACK) up

dev-down: ## Stop and remove the app and worker containers (the database keeps running)
	$(STACK) down

dev-reset: ## dev-down, then delete the stack network and media volume (database untouched)
	$(STACK) reset

dev-status: ## List the mpmv-* containers
	$(STACK) status

dev-logs: ## Follow the app logs (WHAT=worker for the worker)
	$(STACK) logs $(WHAT)

# --- Application
migrate: db ## Apply migrations to the local database
	$(MANAGE) migrate

seed: migrate ## Seed the synthetic demo data (ARC.10); RESET=1 takes it out, docs/dev/demo-seed.md
	$(MANAGE) seed $(if $(RESET),--reset)

run: migrate assets ## Run the development server
	$(MANAGE) runserver

assets: ## Build every theme's tokens.css and theme.css into build/themes/ (THM.2, ADR-0008)
	uv run python -m apps.themes.build

worker: migrate ## Run the background worker: queued jobs and periodic schedules (ADR-0004)
	$(MANAGE) procrastinate worker

# --- Quality gates
# Test suites (ADR-0006): unit = no database; integration = real PostgreSQL; e2e = Playwright.
REPORTS := build/reports

test-unit: ## Unit suite only (no database, no container)
	uv run pytest -m unit

test: db ## Unit + integration suites
	uv run pytest -m "unit or integration" --junitxml=$(REPORTS)/junit.xml \
		--cov --cov-report=term-missing:skip-covered --cov-report=html:$(REPORTS)/coverage

# SEC.5: the security-nightly workflow runs this on CI against its PostgreSQL service. The slow
# cases (ReDoS sweep, malicious-upload corpus, long Hypothesis runs) carry the security_slow
# marker; tests/security reruns with the nightly Hypothesis profile. Exit 5 (nothing collected)
# passes until the first security_slow test lands.
test-security-nightly: ## Slow security suites with the nightly Hypothesis profile (no db target)
	@mkdir -p $(REPORTS)
	HYPOTHESIS_PROFILE=nightly uv run pytest -m security_slow \
		--junitxml=$(REPORTS)/junit-security-slow.xml || [ $$? -eq 5 ]
	if [ -d tests/security ]; then HYPOTHESIS_PROFILE=nightly uv run pytest tests/security \
		-m "unit or integration" --junitxml=$(REPORTS)/junit-security.xml; fi

test-e2e: db ## E2E suite in headless Chromium; screenshots in build/e2e/screenshots/
	uv run pytest -m e2e --junitxml=$(REPORTS)/junit-e2e.xml

# THM.5, spec 14 §14.10: baselines come from the CI runner only (Linux Chromium); the manual
# "Visual baselines" workflow runs this target there and uploads the PNGs.
e2e-baselines: db ## Rewrite the visual baselines in tests/e2e/baselines/ (CI: the Visual baselines workflow)
	MPMV_VISUAL=update uv run pytest -m e2e -k visual_baseline

test-all: test test-e2e ## All three suites

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

theme-gates: ## Theme quality gates: contract, CSP lint, size budgets, fonts, licences (THM.5)
	uv run python -m apps.themes.gates

audit: ## Scan pinned dependencies for known vulnerabilities (pip-audit)
	@mkdir -p build
	uv export --no-emit-project --format requirements-txt > build/requirements.txt
	uvx pip-audit --strict --disable-pip --require-hashes -r build/requirements.txt

# OPS.4: the image SBOM comes from syft in CI (docs/dev/dependency-updates.md).
sbom: ## CycloneDX SBOM of the production Python environment (no dev group) in build/sbom/
	@mkdir -p build/sbom
	UV_PROJECT_ENVIRONMENT=build/sbom/venv uv sync --frozen --no-dev --quiet
	uv run cyclonedx-py environment --pyproject pyproject.toml --mc-type application \
		--of JSON --output-file build/sbom/mpmv-python.cdx.json build/sbom/venv/bin/python

check: lint typecheck imports theme-gates docs-check ## All static checks (no database needed)
	$(MANAGE) check --settings=mpmv.settings.test
	$(MANAGE) makemigrations --check --dry-run --settings=mpmv.settings.test
