from __future__ import annotations

import base64
import json
import logging
import os
import threading
from typing import Optional

from src.graph.config import (
    LLM_BACKEND,
    MODEL_PATH,
    MLX_MODEL_PATH,
    MMPROJ_PATH,
    DRAFT_MODEL_PATH,
    LORA_ADAPTER_PATH,
    SPECULATIVE_MODE,
    PROMPT_LOOKUP_NGRAM,
    PROMPT_LOOKUP_NUM_PRED,
    N_CTX,
    N_GPU_LAYERS,
    N_BATCH,
    KV_CACHE_TYPE,
    CHAT_FORMAT,
    USE_MLOCK,
    PROMPT_CACHE_MB,
)

LOGGER = logging.getLogger(__name__)

_llm_instance = None
_llm_lock = threading.Lock()
_mlx_instance: Optional[tuple] = None
_mlx_prompt_cache: Optional[dict] = None


def _get_mlx_llm() -> tuple:
    global _mlx_instance, _mlx_prompt_cache
    if _mlx_instance is not None:
        return _mlx_instance

    with _llm_lock:
        if _mlx_instance is not None:
            return _mlx_instance

        target_path = MLX_MODEL_PATH or MODEL_PATH
        if not target_path:
            raise RuntimeError(
                "LOKI_MLX_MODEL_PATH (or LOKI_MODEL_PATH) is not set for MLX backend."
            )

        from mlx_lm import load as mlx_load

        LOGGER.info("llm: loading MLX model %s into Apple Silicon Unified Memory", target_path)
        load_kwargs = {"adapter_path": LORA_ADAPTER_PATH} if LORA_ADAPTER_PATH else {}
        model, tokenizer = mlx_load(target_path, **load_kwargs)
        _mlx_instance = (model, tokenizer)
        prompt_cache_obj = None
        if PROMPT_CACHE_MB > 0:
            try:
                from mlx_lm.models.cache import make_prompt_cache

                prompt_cache_obj = make_prompt_cache(model)
            except Exception as exc:
                LOGGER.debug("llm: MLX prompt cache init skipped: %s", exc)
        _mlx_prompt_cache = {"prefix_tokens": None, "cache": prompt_cache_obj}
        LOGGER.info("llm: MLX model loaded and resident in-process")

    return _mlx_instance


def _call_llm_mlx(
    chat_messages: list[dict],
    num_predict: int,
    temperature: float,
    json_format: bool,
    grammar: Optional[object],
) -> str:
    from mlx_lm import generate as mlx_generate

    model, tokenizer = _get_mlx_llm()

    if hasattr(tokenizer, "apply_chat_template"):
        prompt_str = tokenizer.apply_chat_template(
            chat_messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    else:
        prompt_str = "\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in chat_messages) + "\nASSISTANT:\n"

    gen_kwargs: dict = {
        "max_tokens": num_predict,
        "verbose": False,
    }
    if _mlx_prompt_cache and _mlx_prompt_cache.get("cache") is not None:
        gen_kwargs["prompt_cache"] = _mlx_prompt_cache["cache"]
    try:
        from mlx_lm.sample_utils import make_sampler

        gen_kwargs["sampler"] = make_sampler(temp=temperature)
    except ImportError:
        gen_kwargs["temp"] = temperature

    try:
        raw = mlx_generate(model, tokenizer, prompt=prompt_str, **gen_kwargs)
    except TypeError:
        gen_kwargs.pop("prompt_cache", None)
        raw = mlx_generate(model, tokenizer, prompt=prompt_str, **gen_kwargs)
    return (raw or "").strip()


def _apply_chat_format(kwargs: dict) -> None:
    if CHAT_FORMAT == "gemma":
        LOGGER.warning(
            "llm: LOKI_CHAT_FORMAT=gemma drops system prompts and uses the wrong turn "
            "markers for Gemma 4 — using the GGUF's embedded template instead"
        )
        return
    if CHAT_FORMAT and CHAT_FORMAT != "auto":
        kwargs["chat_format"] = CHAT_FORMAT


def _get_llm():
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance

    with _llm_lock:
        if _llm_instance is not None:
            return _llm_instance

        if not MODEL_PATH:
            raise RuntimeError(
                "LOKI_MODEL_PATH is not set. Point it at a local GGUF file, "
                "e.g. LOKI_MODEL_PATH=/path/to/gemma-4-E2B-it-Q4_K_M.gguf"
            )

        from llama_cpp import Llama

        kwargs: dict = dict(
            model_path=MODEL_PATH,
            n_ctx=N_CTX,
            n_gpu_layers=N_GPU_LAYERS,
            n_batch=N_BATCH,
            use_mmap=True,
            use_mlock=USE_MLOCK,
            flash_attn=True,
            type_k=KV_CACHE_TYPE,
            type_v=KV_CACHE_TYPE,
            logits_all=False,
            verbose=False,
        )
        if LORA_ADAPTER_PATH:
            kwargs["lora_path"] = LORA_ADAPTER_PATH

        if MMPROJ_PATH and os.path.isfile(MMPROJ_PATH):
            try:
                from llama_cpp.llama_chat_format import Gemma4ChatHandler

                kwargs["chat_handler"] = Gemma4ChatHandler(
                    clip_model_path=MMPROJ_PATH,
                    verbose=False,
                )
                LOGGER.info("llm: Gemma4ChatHandler loaded with mmproj %s", MMPROJ_PATH)
            except Exception as exc:
                LOGGER.warning(
                    "llm: Gemma4ChatHandler failed, falling back to chat_format=%s: %s",
                    CHAT_FORMAT, exc,
                )
                _apply_chat_format(kwargs)
        else:
            _apply_chat_format(kwargs)

        if "chat_handler" not in kwargs:
            if SPECULATIVE_MODE == "draft_model" and DRAFT_MODEL_PATH:
                try:
                    from llama_cpp import LlamaDraftModel

                    kwargs["draft_model"] = LlamaDraftModel(model_path=DRAFT_MODEL_PATH)
                    LOGGER.info("llm: speculative decoding enabled with draft model %s", DRAFT_MODEL_PATH)
                except Exception as exc:
                    LOGGER.warning("llm: failed to load draft model, continuing without speculative decoding: %s", exc)
            elif SPECULATIVE_MODE == "prompt_lookup":
                try:
                    from llama_cpp.llama_speculative import LlamaPromptLookupDecoding

                    kwargs["draft_model"] = LlamaPromptLookupDecoding(
                        max_ngram_size=PROMPT_LOOKUP_NGRAM,
                        num_pred_tokens=PROMPT_LOOKUP_NUM_PRED,
                    )
                    LOGGER.info(
                        "llm: self-speculative decoding enabled (prompt-lookup, ngram=%d, num_pred=%d)",
                        PROMPT_LOOKUP_NGRAM, PROMPT_LOOKUP_NUM_PRED,
                    )
                except Exception as exc:
                    LOGGER.warning("llm: prompt-lookup decoding unavailable, continuing without speculative decoding: %s", exc)

            if "draft_model" in kwargs:
                kwargs["logits_all"] = True
        else:
            LOGGER.info("llm: speculative decoding skipped (incompatible with multimodal handler)")

        LOGGER.info(
            "llm: loading GGUF model %s (n_ctx=%d, n_gpu_layers=%d)",
            MODEL_PATH, N_CTX, N_GPU_LAYERS,
        )
        _llm_instance = Llama(**kwargs)
        if PROMPT_CACHE_MB > 0 and "chat_handler" not in kwargs:
            from llama_cpp.llama_cache import LlamaRAMCache

            _llm_instance.set_cache(LlamaRAMCache(capacity_bytes=PROMPT_CACHE_MB << 20))
        LOGGER.info("llm: model loaded and resident in-process")

    return _llm_instance


def reset_kv_cache() -> None:
    global _llm_instance
    if _llm_instance is not None:
        try:
            _llm_instance.reset()
        except Exception as exc:
            LOGGER.debug("llm: reset_kv_cache failed (non-fatal): %s", exc)


def call_llm(
    system: str,
    user: str,
    num_predict: int = 2048,
    images: Optional[list[str]] = None,
    temperature: float = 0.0,
    json_format: bool = False,
    grammar: Optional[object] = None,
    messages: Optional[list[dict]] = None,
) -> str:
    if images and MMPROJ_PATH:
        LOGGER.debug("llm: images param present — multimodal handler will process them")
    elif images:
        LOGGER.debug("llm: images param ignored (no MMPROJ_PATH set)")

    if grammar is None and json_format:
        from src.graph.grammars import GENERIC_JSON_OBJECT_GRAMMAR, load_grammar

        grammar = load_grammar(GENERIC_JSON_OBJECT_GRAMMAR)

    chat_messages = messages if messages is not None else [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    if LLM_BACKEND == "mlx" and not images:
        try:
            return _call_llm_mlx(
                chat_messages=chat_messages,
                num_predict=num_predict,
                temperature=temperature,
                json_format=json_format,
                grammar=grammar,
            )
        except Exception as exc:
            LOGGER.error("llm: MLX generation failed: %s", exc)
            raise

    llm = _get_llm()

    call_kwargs: dict = dict(
        messages=chat_messages,
        max_tokens=num_predict,
        temperature=temperature,
    )
    if grammar is not None:
        call_kwargs["grammar"] = grammar

    try:
        response = llm.create_chat_completion(**call_kwargs)
        content = response["choices"][0]["message"]["content"].strip()
        if not content:
            LOGGER.warning(
                "LLM empty response: finish_reason=%s",
                response["choices"][0].get("finish_reason"),
            )
        return content
    except Exception as exc:
        LOGGER.error("llm: create_chat_completion failed: %s", exc)
        raise


def call_llm_audio(
    wav_path: str,
    prompt: str = "Transcribe this spoken command exactly. Output only the user's instruction, nothing else.",
    num_predict: int = 512,
    temperature: float = 0.0,
) -> str:
    if not MMPROJ_PATH:
        raise RuntimeError(
            "call_llm_audio requires LOKI_MMPROJ_PATH to be set "
            "(Gemma4ChatHandler with audio support)"
        )

    import os

    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    ext = os.path.splitext(wav_path)[1].lower()
    fmt_map = {".wav": "wav", ".mp3": "mp3", ".flac": "flac"}
    audio_format = fmt_map.get(ext, "wav")

    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")

    LOGGER.info("llm: transcribing audio %s (%s, %d b64 chars)", wav_path, audio_format, len(audio_b64))

    llm = _get_llm()
    response = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_b64,
                            "format": audio_format,
                        },
                    },
                ],
            },
        ],
        max_tokens=num_predict,
        temperature=temperature,
    )

    content = response["choices"][0]["message"]["content"].strip()
    LOGGER.info("llm: audio transcription result: %s", content[:120])
    return content


def call_llm_vision(
    img_path: str,
    prompt: str = "Describe all visible text on this screen.",
    system: str = "You are a screen reader. Extract and list all visible text from the image.",
    num_predict: int = 2048,
    temperature: float = 0.0,
) -> str:
    if not MMPROJ_PATH:
        raise RuntimeError(
            "call_llm_vision requires LOKI_MMPROJ_PATH to be set "
            "(Gemma4ChatHandler with vision support)"
        )

    import os

    if not os.path.isfile(img_path):
        raise FileNotFoundError(f"Image file not found: {img_path}")

    ext = os.path.splitext(img_path)[1].lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    mime = mime_map.get(ext, "image/png")

    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")

    data_uri = f"data:{mime};base64,{img_b64}"

    LOGGER.info("llm: vision analysis of %s (%d b64 chars)", img_path, len(img_b64))

    llm = _get_llm()
    response = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            },
        ],
        max_tokens=num_predict,
        temperature=temperature,
    )

    content = response["choices"][0]["message"]["content"].strip()
    LOGGER.info("llm: vision result: %s", content[:120])
    return content


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    start = raw.find("{")
    if start == -1:
        return {}
    depth, end = 0, start
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return {}
