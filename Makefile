ORCH_DIR      := orchestration
INTERFACE_DIR := interface/loki-macos
MODELS_DIR    := models

HOST := 127.0.0.1
PORT ?= 9820

LLM_REPO   ?= unsloth/gemma-4-E2B-it-GGUF
LLM_FILES  ?= gemma-4-E2B-it-Q4_K_M.gguf mmproj-BF16.gguf

UV := uv run --frozen --project $(ORCH_DIR)

.DEFAULT_GOAL := help
.PHONY: help setup login models llm tts stt embeddings verify test lint build server interface app open-app dev

help:
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python dependencies from uv.lock
	cd $(ORCH_DIR) && uv sync --frozen

login: ## Log in to Hugging Face (needed for gated models such as EmbeddingGemma)
	$(UV) hf auth login

models: llm tts stt ## Download all models (LLM, TTS, STT)

llm: ## Download the Gemma 4 GGUF and mmproj into ./models
	mkdir -p $(MODELS_DIR)
	$(UV) hf download $(LLM_REPO) $(LLM_FILES) --local-dir $(MODELS_DIR)

tts: ## Download Kokoro-82M 4-bit per model-manifests/*.json and verify SHA-256
	cd $(ORCH_DIR) && PYTHONPATH=. uv run --frozen python scripts/prepare_tts_model.py

stt: ## Download Moonshine tiny + small streaming and verify CRC32C
	cd $(ORCH_DIR) && PYTHONPATH=. uv run --frozen python scripts/prepare_stt_model.py

embeddings: ## Download EmbeddingGemma-300M (gated: accept the terms and run `hf auth login` first)
	$(UV) hf download google/embeddinggemma-300m

verify: ## Check downloaded model files against their manifests (no download)
	cd $(ORCH_DIR) && PYTHONPATH=. uv run --frozen python scripts/verify_models.py

test: ## Run the Python tests
	cd $(ORCH_DIR) && uv run --frozen pytest

lint: ## Lint the Python code with Ruff
	cd $(ORCH_DIR) && uv run --frozen ruff check .

build: ## Compile the macOS app (debug build, no launch)
	cd $(INTERFACE_DIR) && swift build

server: ## Run the orchestration server (PORT=9820)
	cd $(ORCH_DIR) && uv run --frozen python server.py --port $(PORT)

interface: ## Build and run the macOS app (uses PORT)
	cd $(INTERFACE_DIR) && LOKI_ORCHESTRATION_BASE=http://$(HOST):$(PORT) swift run

app: ## Build Loki.app into interface/loki-macos/.build/Loki.app
	$(INTERFACE_DIR)/scripts/bundle.sh

open-app: app ## Build Loki.app and open it
	open $(INTERFACE_DIR)/.build/Loki.app

dev: ## Start the server, wait for /health, then run the macOS app
	@set -e; \
	(cd $(ORCH_DIR) && exec uv run --frozen python server.py --port $(PORT)) & \
	pid=$$!; \
	trap 'pkill -P $$pid 2>/dev/null || true; kill $$pid 2>/dev/null || true' EXIT INT TERM; \
	echo "Waiting for http://$(HOST):$(PORT)/health ..."; \
	for i in $$(seq 1 120); do \
		curl -sf http://$(HOST):$(PORT)/health >/dev/null && break; \
		kill -0 $$pid 2>/dev/null || { echo "server exited early" >&2; exit 1; }; \
		sleep 1; \
	done; \
	curl -sf http://$(HOST):$(PORT)/health >/dev/null || { echo "server not healthy after 120s" >&2; exit 1; }; \
	cd $(INTERFACE_DIR) && LOKI_ORCHESTRATION_BASE=http://$(HOST):$(PORT) swift run
