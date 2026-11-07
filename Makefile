# piac task runner. Application targets (dev, test, check, seed) arrive with ARC.3/ARC.5.
.DEFAULT_GOAL := help
.PHONY: help docs docs-check docs-open

help: ## List available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-12s %s\n", $$1, $$2}'

docs: ## Regenerate backlog (md/csv) and the single-page HTML spec in build/
	uv run tools/build_docs.py

docs-check: ## Fail if the committed backlog is out of date with the spec tables
	uv run tools/build_docs.py --check

docs-open: docs ## Build and open the HTML spec
	open build/piac-specifica.html
