COMPOSE := docker compose -f docker-compose.dev.yml
PROD_COMPOSE := docker compose -f docker-compose.prod.yml

ifneq (,$(wildcard .env.local))
include .env.local
endif

REMOTE_LC ?=
REMOTE_LC_USER ?=
REMOTE_LC_PATH ?=

.PHONY: start stop restart rebuild ssh deploy dev-up dev-down dev-build logs mail-api-shell \
        prod-up prod-down prod-build prod-restart prod-logs dev-setup npm-publish

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
