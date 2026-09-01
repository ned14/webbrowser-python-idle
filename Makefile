SHELL := /bin/sh

# The storage backend for BUILD steps. docker compose reads .env directly; the
# build must resolve the same way, or the guest image, frontend and containers
# silently disagree with the deployment — e.g. a browser-mode guest image in a
# webdav deployment, where the sync agent never runs (fixed 2026-08-15).
# Precedence: command line > environment > .env > browser. The .env layer is
# resolved through the SHARED lib (scripts/lib/webvm-common.sh — one loader
# rules them all: the entrypoints, scripts and this Makefile all use
# webvm_load_dotenv, which never overrides an explicit environment value, and
# compose strips .env quotes identically). A make command-line override or
# exported env var is an explicit environment value and wins; the `?=` below
# keeps make from clobbering either.
# ONE shell invocation resolves the backend + data dir + image dir/name (the
# same values build.sh produces and nginx serves — single home: the lib).
_ENV_RESOLVED := $(shell WEBVM_COMMON=scripts/lib/webvm-common.sh sh -c '. "$$WEBVM_COMMON"; webvm_load_dotenv; printf "%s|%s|%s|%s" "$$STORAGE_BACKEND" "$$DATA_DIR" "$$WEBVM_IMAGE_DIR" "$$WEBVM_IMAGE_NAME"')
_ENV_BACKEND := $(word 1,$(subst |, ,$(_ENV_RESOLVED)))
_ENV_DATA_DIR := $(word 2,$(subst |, ,$(_ENV_RESOLVED)))
_ENV_IMAGE_DIR := $(word 3,$(subst |, ,$(_ENV_RESOLVED)))
_ENV_IMAGE_NAME := $(word 4,$(subst |, ,$(_ENV_RESOLVED)))
STORAGE_BACKEND ?= $(_ENV_BACKEND)
WEBVM_IMAGE_DIR ?= $(_ENV_IMAGE_DIR)
WEBVM_IMAGE_NAME ?= $(_ENV_IMAGE_NAME)

# The mode docker compose will actually deploy (it reads .env, never make
# variables) — used by the up/up-tailnet consistency guard below. Same
# precedence as STORAGE_BACKEND (an explicit env/CLI value wins over .env).
DEPLOY_BACKEND := $(STORAGE_BACKEND)

# The server-side WebDAV sync root (compose mount ${DATA_DIR:-./data} ->
# ${WEBDAV_ROOT:-/data/webdav}). Resolved from .env for reset-webdav.
DATA_DIR ?= $(_ENV_DATA_DIR)

.PHONY: certs build check-image-backend check-image-build up up-tailnet down logs test test-unit test-frontend acceptance url clean reset-webdav reset-cycle

## Generate the private CA + server cert (once; browser trust is a manual step)
certs:
	./scripts/gen-certs.sh

## Build the guest ext2 image, the frontend (with the image fingerprint), then
## the container images. STORAGE_BACKEND resolves from .env (command line /
## environment override it) so the image mode always matches the deployment.
## webvm deps are installed automatically when the pinned vite binary is absent
## (a fresh checkout): `npm run build` would otherwise fall back to PATH and a
## bare machine can win an unrelated system "vite" (a Qt GUI app that crashes/
## hangs opening the `build` argument). Requires Node ^20.19.0 or >=22.12.0:
## npm ci pulls vitest 4 -> rolldown, which enforces that engine range.
build:
	@echo "==> Building for backend '$(STORAGE_BACKEND)' (deployment mode: $(DEPLOY_BACKEND))"
	./build.sh $(STORAGE_BACKEND)
	@cd webvm; if [ ! -x node_modules/.bin/vite ]; then \
		node -e "const [p,q]=process.versions.node.split('.').map(Number); if (!((p===20&&q>=19)||(p===22&&q>=12)||p>22)) { console.error('ERROR: webvm build requires Node ^20.19.0 or >=22.12.0 (npm ci pulls vitest 4 -> rolldown, which enforces it); found '+process.versions.node+'. Install Node 22.12+ or newer, then re-run make build.'); process.exit(1) }"; \
		npm ci --no-audit --no-fund; \
	fi
	cd webvm && WEBVM_MODE=$(STORAGE_BACKEND) WEBVM_IMAGE_BUILD=$$(cat ../webvm/$(WEBVM_IMAGE_DIR)/image-build.txt 2>/dev/null || echo dev) WEBVM_COMMIT=$$(git rev-parse HEAD 2>/dev/null || true) WEBVM_COMMIT_DATE=$$(git show -s --format=%cs HEAD 2>/dev/null || true) npm run build
	docker compose build
	@echo ""
	@echo "==> Built image sizes:"
	@echo "   guest ext2:      webvm/$(WEBVM_IMAGE_DIR)/$(WEBVM_IMAGE_NAME) ($$(du -h webvm/$(WEBVM_IMAGE_DIR)/$(WEBVM_IMAGE_NAME) | cut -f1))"
	@echo "                     The only Linux image served to browsers (same-origin byte-range, /$(WEBVM_IMAGE_DIR)/)."
	@echo "   guest docker:    webvm-guest ($$(docker image inspect webvm-guest --format '{{.Size}}' | awk '{printf "%.0f MiB\n", $$1/1048576}'))"
	@echo "                     Docker build artifact only — never served (includes multi-stage shimbuild + layer history)."

## Fail unless the built guest image matches the deployment mode (.env). A
## mismatch silently disables the mode's guest-side features (e.g. the sync
## agent in webdav) — the 2026-08-15 build-consistency fix.
check-image-backend:
	@if [ ! -f webvm/$(WEBVM_IMAGE_DIR)/image-backend.txt ]; then \
		echo "ERROR: no built guest image marker (webvm/$(WEBVM_IMAGE_DIR)/image-backend.txt)." >&2; \
		echo "       This artifact predates the backend-consistency check; run 'make build' first." >&2; \
		exit 1; \
	fi
	@built=$$(cat webvm/$(WEBVM_IMAGE_DIR)/image-backend.txt); \
	if [ "$$built" != "$(DEPLOY_BACKEND)" ]; then \
		echo "ERROR: the built guest image is for backend '$$built' but the deployment (.env) is '$(DEPLOY_BACKEND)'." >&2; \
		echo "       Run 'make build' to rebuild the guest image, frontend and containers for '$(DEPLOY_BACKEND)'." >&2; \
		exit 1; \
	fi

## Fail unless the built guest image exists (shared by up/up-tailnet)
check-image-build:
	@if [ ! -f webvm/$(WEBVM_IMAGE_DIR)/image-build.txt ]; then \
		echo "ERROR: no guest image build found. Run 'make build' first (builds the ext2, the frontend and the container images)." >&2; \
		exit 1; \
	fi

## Start the stack (browser/none: nginx only; samba/webdav: also needs make up-tailnet)
## `up` is a HARD-NETWORKLESS launch: whatever .env contains, the page boots
## fully disconnected (empty baked config, no headscale, sidebar Networking
## crossed out and disabled, zero tailnet connection attempts).
up: certs check-image-backend check-image-build
	@if [ "$(DEPLOY_BACKEND)" = "samba" ] || [ "$(DEPLOY_BACKEND)" = "webdav" ]; then \
		echo "NOTE: deployment backend is '$(DEPLOY_BACKEND)' but \`make up\` is a HARD-NETWORKLESS" >&2; \
		echo "      launch (WEBVM_TAILNET=off) — the stack will boot fully disconnected." >&2; \
		echo "      Use 'make up-tailnet' for the tailnet-capable launch." >&2; \
	fi
	WEBVM_TAILNET=off docker compose up -d

## Start the stack including the gateway — the ONLY tailnet-capable launch
up-tailnet: certs check-image-backend check-image-build
	WEBVM_TAILNET=on docker compose --profile tailnet up -d

down:
	docker compose --profile tailnet down

logs:
	docker compose --profile tailnet logs -f

## Print the session URL(s) for the current deployment (OPTIONAL: tailnet
## modes bake the keys into the served page at container start, so visiting
## the site root auto-wires the tailnet; the hash URL is for other devices
## and explicit overrides). The script loads .env itself (environment wins),
## so no sourcing here — a `VAR=x make url` override must not be clobbered.
url:
	./scripts/print-url.sh

## Run the unit tests (compose test profile, no host Python needed)
test-unit:
	docker compose --profile test run --rm test-unit

## Run the frontend unit tests (vitest: cacheId/sessionGuard/session seed/
## network states + watchdog/clipboard paste contract)
test-frontend:
	cd webvm && npm test

## Run the full local test suite (unit, frontend unit, rootfs, server
## integration, E2E)
test: test-unit test-frontend
	@echo ""
	@echo "Layered tests:"
	@echo "  tests/rootfs/  — docker run webvm-guest (needs a built guest image)"
	@echo "  tests/server/  — docker compose integration (needs make up)"
	@echo "  tests/e2e/     — Playwright in-browser E2E (needs make up)"
	@echo "Run them per tests/README.md after 'make build && make up'."

## Manual / LAN acceptance checklist
acceptance:
	./scripts/acceptance.sh

## Tear everything down (including volumes)
clean:
	docker compose --profile tailnet down -v --remove-orphans

## Reset the WebDAV sync storage on the server: stop the stack, wipe the
## webdav root (${DATA_DIR:-./data}, mounted at the container's
## ${WEBDAV_ROOT:-/data/webdav}), and restart the full stack (tailnet profile
## — webdav always needs the gateway). The guest re-seeds the storage on its
## next boot pull. Headscale data (gateway node + keys) is untouched, so
## GATEWAY_TAILNET_IP stays valid.
reset-webdav: down
	@if [ "$(DEPLOY_BACKEND)" != "webdav" ]; then \
		echo "ERROR: deployment backend is '$(DEPLOY_BACKEND)', not webdav — nothing to reset." >&2; \
		exit 1; \
	fi
	@mkdir -p "$(DATA_DIR)"
	@rm -rf "$(DATA_DIR)"/* "$(DATA_DIR)"/.[!.]* "$(DATA_DIR)"/..?* 2>/dev/null || true
	@echo "==> WebDAV storage reset at $(DATA_DIR)"
	docker compose --profile tailnet up -d

## Run ONE periodic storage-reset cycle for a public instance (host cron):
## stop the stack, wipe the webdav storage, pull the latest commit, rebuild,
## record the next countdown deadline, and restore. OPT-IN: requires
## RESET_INTERVAL_HOURS in .env. See scripts/reset-cycle.sh.
reset-cycle:
	./scripts/reset-cycle.sh
