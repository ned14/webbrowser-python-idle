SHELL := /bin/sh

# The storage backend for BUILD steps. docker compose reads .env directly; the
# build must resolve the same way, or the guest image, frontend and containers
# silently disagree with the deployment — e.g. a browser-mode guest image in a
# webdav deployment, where the sync agent never runs (fixed 2026-08-15).
# Precedence: command line > environment > .env > browser.
_ENV_BACKEND := $(shell [ -f .env ] && sed -n 's/^[[:space:]]*STORAGE_BACKEND[[:space:]]*=[[:space:]]*//p' .env | tail -1)
STORAGE_BACKEND ?= $(if $(_ENV_BACKEND),$(_ENV_BACKEND),browser)

# The mode docker compose will actually deploy (it reads .env, never make
# variables) — used by the up/up-tailnet consistency guard below.
DEPLOY_BACKEND := $(if $(_ENV_BACKEND),$(_ENV_BACKEND),browser)

# The server-side WebDAV sync root (compose mount ${DATA_DIR:-./data} ->
# ${WEBDAV_ROOT:-/data/webdav}). Resolved from .env for reset-webdav.
_ENV_DATA_DIR := $(shell [ -f .env ] && sed -n 's/^[[:space:]]*DATA_DIR[[:space:]]*=[[:space:]]*//p' .env | tail -1)
DATA_DIR ?= $(if $(_ENV_DATA_DIR),$(_ENV_DATA_DIR),./data)

.PHONY: certs build check-image-backend up up-tailnet down logs test test-unit acceptance url clean reset-webdav

## Generate the private CA + server cert (once; browser trust is a manual step)
certs:
	./scripts/gen-certs.sh

## Build the guest ext2 image, the frontend (with the image fingerprint), then
## the container images. STORAGE_BACKEND resolves from .env (command line /
## environment override it) so the image mode always matches the deployment.
build:
	@echo "==> Building for backend '$(STORAGE_BACKEND)' (deployment mode: $(DEPLOY_BACKEND))"
	./build.sh $(STORAGE_BACKEND)
	cd webvm && WEBVM_MODE=$(STORAGE_BACKEND) WEBVM_IMAGE_BUILD=$$(cat ../webvm/custom-disk-images/image-build.txt 2>/dev/null || echo dev) npm run build
	docker compose build

## Fail unless the built guest image matches the deployment mode (.env). A
## mismatch silently disables the mode's guest-side features (e.g. the sync
## agent in webdav) — the 2026-08-15 build-consistency fix.
check-image-backend:
	@if [ ! -f webvm/custom-disk-images/image-backend.txt ]; then \
		echo "ERROR: no built guest image marker (webvm/custom-disk-images/image-backend.txt)." >&2; \
		echo "       This artifact predates the backend-consistency check; run 'make build' first." >&2; \
		exit 1; \
	fi
	@built=$$(cat webvm/custom-disk-images/image-backend.txt); \
	if [ "$$built" != "$(DEPLOY_BACKEND)" ]; then \
		echo "ERROR: the built guest image is for backend '$$built' but the deployment (.env) is '$(DEPLOY_BACKEND)'." >&2; \
		echo "       Run 'make build' to rebuild the guest image, frontend and containers for '$(DEPLOY_BACKEND)'." >&2; \
		exit 1; \
	fi

## Start the stack (browser/none: nginx only; samba/webdav: also needs make up-tailnet)
up: certs check-image-backend
	@if [ ! -f webvm/custom-disk-images/image-build.txt ]; then \
		echo "ERROR: no guest image build found. Run 'make build' first (builds the ext2, the frontend and the container images)." >&2; \
		exit 1; \
	fi
	docker compose up -d

## Start the stack including the gateway (tailnet modes)
up-tailnet: certs check-image-backend
	@if [ ! -f webvm/custom-disk-images/image-build.txt ]; then \
		echo "ERROR: no guest image build found. Run 'make build' first (builds the ext2, the frontend and the container images)." >&2; \
		exit 1; \
	fi
	docker compose --profile tailnet up -d

down:
	docker compose --profile tailnet down

logs:
	docker compose --profile tailnet logs -f

## Print the session URL(s) for the current deployment (OPTIONAL: tailnet
## modes bake the keys into the served page at container start, so visiting
## the site root auto-wires the tailnet; the hash URL is for other devices
## and explicit overrides)
url:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; ./scripts/print-url.sh

## Run the unit tests (compose test profile, no host Python needed)
test-unit:
	docker compose --profile test run --rm test-unit

## Run the full local test suite (unit, rootfs, server integration, E2E)
test: test-unit
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
