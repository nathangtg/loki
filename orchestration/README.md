# LOKI Orchestration

LangGraph-based agent orchestration for LOKI's computer-use pipeline.

## LLM Backend (llama-cpp-python)

LOKI runs its LLM in-process via `llama-cpp-python`; no separate model
server is needed. Point `LOKI_MODEL_PATH` at a local GGUF file (`make llm`
downloads one into `models/`):

```bash
export LOKI_MODEL_PATH=/path/to/gemma-4-e4b-Q4_K_M.gguf
export LOKI_DRAFT_MODEL_PATH=/path/to/gemma-2b-it-Q8_0.gguf  # optional — speculative decoding
export LOKI_N_CTX=8192
export LOKI_N_GPU_LAYERS=-1   # -1 = offload all layers to Metal/GPU
export LOKI_CHAT_FORMAT=gemma
```

On macOS, install with Metal acceleration:

```bash
CMAKE_ARGS="-DLLAMA_METAL=ON" uv pip install llama-cpp-python --force-reinstall --no-cache-dir
```

Run the server:

```bash
uv run python server.py
```

## Speech-to-Text (Moonshine STT)

LOKI uses Useful Sensors' **Moonshine Voice** for on-device streaming STT on Apple Silicon.
STT runs on the ONNX CPU runtime, keeping the GPU and ANE 100% free for the LLM.

- **Small Streaming**: 2.61% WER (production quality, ~142 MB)
- **Tiny Streaming**: 4.83% WER (fast/lightweight, ~45 MB)

`make stt` downloads the models and verifies their CRC32C checksums; `make verify` re-checks them later.

## Text-to-Speech (Kokoro 82M 4-bit)

LOKI pins `mlx-audio==0.5.4`, `misaki[en]==0.9.4`, and the `en-core-web-sm==3.8.0` language model for native Apple Silicon inference. Provision the selected model and `af_heart` voice in an explicit installation step:

```bash
cd orchestration
uv sync --frozen
PYTHONPATH=. uv run python scripts/prepare_tts_model.py
```

The script downloads only the files listed in `model-manifests/kokoro-82m-4bit.json` from the pinned Hugging Face revision and verifies every byte count and SHA-256 digest. The verified artifact set is 283,281,936 bytes. Set `LOKI_TTS_MODEL_PATH` to use a packaged location instead of `orchestration/weights/tts/kokoro-82m-4bit`.

Production startup obtains the resident adapter through `src.server.config.get_tts_adapter()` and calls `load()`. It rejects a missing or modified artifact before calling MLX and never passes a model-hub identifier to `mlx-audio`, so startup cannot download model or voice files implicitly.

```python
adapter = get_tts_adapter()
stream = adapter.synthesize(response_text, session_id)
for chunk in stream:
    playback.write(chunk.pcm_f32le, chunk.valid_samples)
```

Each chunk is one 20 ms frame containing 480 mono little-endian `f32` samples at 24 kHz (1,920 bytes). A final partial frame is zero-padded and reports its meaningful length through `valid_samples`. Call `adapter.cancel(stream.generation_id)` to stop a generation, or `adapter.reset()` to invalidate all outstanding streams while retaining the loaded model.

### macOS system speech fallback

`src.server.config.get_system_tts_adapter()` provides speech before Kokoro is installed or whenever its warmup fails. Configure the optional voice and rate with `LOKI_SYSTEM_TTS_VOICE` and `LOKI_SYSTEM_TTS_RATE`; the server's default rate is 185 words per minute.

The fallback launches `/usr/bin/say` directly with an argument vector and writes response text to the child's standard input. It never invokes a shell, logs the text, or creates an audio file. Cancellation terminates only the `Popen` instance owned by the active generation, waits 250 ms, and kills that same child if it does not exit.

## Speculative Decoding

Enabled by default via `LOKI_SPECULATIVE_MODE=prompt_lookup` — n-gram
self-speculation with **no extra model required**, well-suited to LOKI's
repetitive JSON action output. Set `LOKI_DRAFT_MODEL_PATH` +
`LOKI_SPECULATIVE_MODE=draft_model` to use a classic draft model instead
(e.g. Gemma 2B alongside a larger main model).

## Model Benchmarking (SLM specialization)

LOKI's reason node is a structured classification task (pick 1 of 12
action types), not a reasoning-heavy task — small models often match
larger ones here at a fraction of the latency. Compare candidates with:

```bash
LOKI_MODEL_PATH=/path/to/gemma-4-e4b-Q4_K_M.gguf LOKI_CHAT_FORMAT=gemma \
    uv run python scripts/benchmark_models.py --label "Gemma-4-E4B"

LOKI_MODEL_PATH=/path/to/qwen2.5-1.5b-instruct-q4.gguf LOKI_CHAT_FORMAT=chatml \
    uv run python scripts/benchmark_models.py --label "Qwen2.5-1.5B"
```

Each model runs in its own process (the `Llama` singleton loads once per
process from `LOKI_MODEL_PATH`).

## Prompt Compression (optional)

Set `LOKI_PROMPT_COMPRESSION=1` to enable llmlingua-2 compression of long
`page_text` blocks before they're injected into the reason prompt.
Disabled by default — it loads an extra ~500MB transformer model on
first use, so only enable it once you've confirmed the latency tradeoff
is worth it for your workload.
