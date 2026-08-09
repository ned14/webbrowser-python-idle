SHELL := /bin/sh

STORAGE_BACKEND ?= browser

.PHONY: certs build frontend up up-tailnet down logs test test-unit acceptance url clean

## Generate the private CA + server cert (once; browser trust is a manual step)
certs:
	./scripts/gen-certs.sh

## Build the guest ext2 image, the frontend (with the image fingerprint), then
## the container images
build:
	./build.sh $(STORAGE_BACKEND)
	cd webvm && WEBVM_MODE=$(STORAGE_BACKEND) WEBVM_IMAGE_BUILD=$$(cat ../webvm/custom-disk-images/image-build.txt 2>/dev/null || echo dev) npm run build
	docker compose build

## Start the stack (browser/none: nginx only; samba/webdav: also needs make up-tailnet)
up: certs
	@if [ ! -f webvm/custom-disk-images/image-build.txt ]; then \
		echo "ERROR: no guest image build found. Run 'make build' first (builds the ext2, the frontend and the container images)." >&2; \
		exit 1; \
	fi
	docker compose up -d

## Start the stack including the gateway (tailnet modes)
up-tailnet: certs
	@if [ ! -f webvm/custom-disk-images/image-build.txt ]; then \
		echo "ERROR: no guest image build found. Run 'make build' first (builds the ext2, the frontend and the container images)." >&2; \
		exit 1; \
	fi
	docker compose --profile tailnet up -d

down:
	docker compose --profile tailnet down

logs:
	docker compose --profile tailnet logs -f

## Print the session URL(s) for the current deployment
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
