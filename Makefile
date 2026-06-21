COMPOSE := docker compose -f docker-compose.dev.yml
PROD_COMPOSE := docker compose -f docker-compose.prod.yml

ifneq (,$(wildcard .env.local))
include .env.local
endif

REMOTE_LC ?=
REMOTE_LC_USER ?=
REMOTE_LC_PATH ?=

.PHONY: start stop restart rebuild ssh deploy dev-up dev-down dev-build logs mail-api-shell \
        prod-up prod-down prod-build prod-restart prod-logs dev-setup npm-publish migrate-status push \
        lint format typecheck typecheck-mcp typecheck-ratchet check

# --- Development environment ---

dev-setup:
	./venv/bin/pip install -r requirements-dev.txt
	./venv/bin/playwright install chromium

# --- Development targets (docker-compose.dev.yml) ---

dev-build:
	$(COMPOSE) build

dev-up:
	@mkdir -p data/caches data/logs data/import_uploads data/radicale
	$(COMPOSE) up -d

dev-down:
	$(COMPOSE) down

restart: dev-down dev-build dev-up

rebuild: dev-build

start: dev-up

stop: dev-down

ssh:
	$(COMPOSE) exec app /bin/bash

mail-api-shell:
	$(COMPOSE) exec mail-api /bin/bash

logs:
	$(COMPOSE) logs -f

logs-app:
	$(COMPOSE) logs -f app

logs-mail:
	$(COMPOSE) logs -f dovecot postfix mail-api

# Show applied migrations for the main app DB (inside the app container).
# Cache DB migrations are per-account and run automatically on open_cache.
migrate-status:
	$(COMPOSE) exec app python -c "\
from app import create_app; from app.shared.db import db; \
app = create_app(); \
app.app_context().push(); \
rows = db.engine.raw_connection().execute('SELECT name, applied_at FROM _schema_migrations ORDER BY name').fetchall(); \
print('Applied main-DB migrations (%d):' % len(rows)); \
[print('  %s  %s' % (r[0], r[1])) for r in rows]"

# --- Production targets (docker-compose.prod.yml) ---

prod-build:
	$(PROD_COMPOSE) build

prod-up:
	$(PROD_COMPOSE) up -d

prod-down:
	$(PROD_COMPOSE) down

prod-restart: prod-down prod-build prod-up

prod-logs:
	$(PROD_COMPOSE) logs -f

# --- Deploy ---

deploy:
	@test -n "$(REMOTE_LC)" || { echo "ERROR: REMOTE_LC is not set. Set it in .env.local or pass via command line." >&2; exit 1; }
	@test -n "$(REMOTE_LC_USER)" || { echo "ERROR: REMOTE_LC_USER is not set. Set it in .env.local or pass via command line." >&2; exit 1; }
	@test -n "$(REMOTE_LC_PATH)" || { echo "ERROR: REMOTE_LC_PATH is not set. Set it in .env.local or pass via command line." >&2; exit 1; }
	npm --prefix packages/locoroosuite-mcp run build
	ssh $(REMOTE_LC) "cd $(REMOTE_LC_PATH) && sudo -u $(REMOTE_LC_USER) git pull && make prod-restart"

# npm-publish: Publishes the locoroosuite-mcp package to npm.
#
# Requires a granular access token (bypasses 2FA):
#   1. Go to https://www.npmjs.com/settings/<username>/tokens
#   2. Generate New Token → Granular Access Token
#   3. Set permissions to Read and write for the locoroosuite-mcp package
#   4. Add to ~/.npmrc:
#      echo "//registry.npmjs.org/:_authToken=YOUR_TOKEN" >> ~/.npmrc
#
npm-publish:
	@echo "==> Cleaning previous build"
	rm -rf packages/locoroosuite-mcp/dist
	@echo "==> Building"
	cd packages/locoroosuite-mcp && npm run build
	@echo "==> Running tests"
	cd packages/locoroosuite-mcp && npm test
	@echo "==> Previewing tarball"
	cd packages/locoroosuite-mcp && npm pack --dry-run
	@echo "==> Publishing to npm"
	cd packages/locoroosuite-mcp && npm publish
	@echo "==> Done"

# --- Push ---

# push: Push the current branch and all tags to origin, codeberg, and github.
# Remotes that are not configured on the local clone are skipped (useful for forks).
# Push order: origin (self-hosted primary) first, then the public mirrors.
PUSH_REMOTES ?= origin codeberg github

push:
	@for remote in $(PUSH_REMOTES); do \
		if git remote get-url $$remote >/dev/null 2>&1; then \
			echo "==> $$remote: branch + tags"; \
			git push $$remote && git push $$remote --tags; \
		else \
			echo "--> $$remote: not configured, skipping"; \
		fi; \
	done
	@echo "==> Push complete"

# --- Static checks (scoped to files touched vs BASE_REF, default: master) ---
# See AGENTS.md -> "Static Checks (last step)" for the policy these enforce.
BASE_REF ?= master
RF := ./venv/bin/ruff
PYRIGHT := ./venv/bin/pyright
TOUCHED_PY := $(shell { git diff --name-only --diff-filter=AM $(BASE_REF) -- '*.py' 2>/dev/null; \
                       git ls-files --others --exclude-standard -- '*.py' 2>/dev/null; } | sort -u)

lint:
	@if [ -z "$(TOUCHED_PY)" ]; then \
		echo "lint: no touched .py files vs $(BASE_REF)"; \
	else \
		$(RF) check $(TOUCHED_PY); \
	fi

format:
	@if [ -z "$(TOUCHED_PY)" ]; then \
		echo "format: no touched .py files vs $(BASE_REF)"; \
	else \
		$(RF) check --fix $(TOUCHED_PY); \
		$(RF) format $(TOUCHED_PY); \
	fi

typecheck:
	@if [ -z "$(TOUCHED_PY)" ]; then \
		echo "typecheck: no touched .py files vs $(BASE_REF)"; \
	else \
		$(PYRIGHT) $(TOUCHED_PY); \
	fi

typecheck-mcp:
	npm run build --prefix packages/locoroosuite-mcp

# Enforce the "strictly cleaner" pyright ratchet on touched files.
typecheck-ratchet:
	./scripts/typecheck_ratchet.sh $(BASE_REF)

# Convenience: lint + typecheck on touched files.
check: lint typecheck
