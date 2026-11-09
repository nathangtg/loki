from __future__ import annotations

import os
from typing import Any

from typing_extensions import TypedDict

LLM_BACKEND = os.getenv("LOKI_LLM_BACKEND", "llamacpp").strip().lower()
MODEL_PATH = os.getenv("LOKI_MODEL_PATH", "")
MLX_MODEL_PATH = os.getenv("LOKI_MLX_MODEL_PATH", MODEL_PATH)
MMPROJ_PATH = os.getenv("LOKI_MMPROJ_PATH", "")
DRAFT_MODEL_PATH = os.getenv("LOKI_DRAFT_MODEL_PATH", "")
LORA_ADAPTER_PATH = os.getenv("LOKI_LORA_PATH", "")

SPECULATIVE_MODE = os.getenv("LOKI_SPECULATIVE_MODE", "none" if not DRAFT_MODEL_PATH else "draft_model")
PROMPT_LOOKUP_NGRAM = int(os.getenv("LOKI_PROMPT_LOOKUP_NGRAM", "2"))
PROMPT_LOOKUP_NUM_PRED = int(os.getenv("LOKI_PROMPT_LOOKUP_NUM_PRED", "10"))
N_CTX = int(os.getenv("LOKI_N_CTX", "2048"))
N_GPU_LAYERS = int(os.getenv("LOKI_N_GPU_LAYERS", "-1"))
N_BATCH = int(os.getenv("LOKI_N_BATCH", "256"))
KV_CACHE_TYPE = int(os.getenv("LOKI_KV_TYPE", "8"))
CHAT_FORMAT = os.getenv("LOKI_CHAT_FORMAT", "auto")
PROMPT_CACHE_MB = int(os.getenv("LOKI_PROMPT_CACHE_MB", "0"))
USE_MLOCK = os.getenv("LOKI_MLOCK", "0") in ("1", "true", "True")
PROMPT_COMPRESSION_ENABLED = os.getenv("LOKI_PROMPT_COMPRESSION", "0") in ("1", "true", "True")
TERSE_SYSTEM_ENABLED = os.getenv("LOKI_TERSE_SYSTEM", "0") in ("1", "true", "True")

MAX_TURNS = 25


class LokiState(TypedDict, total=False):
    goal: str
    on_step: Any
    on_message: Any
    ask_user: Any

    plan: list[str]
    plan_step: int
    plan_reasoning: str

    turn: int
    history: list[dict]
    next_action: dict
    done: bool
    user_answer: str

    current_url: str
    page_title: str
    page_text: str
    screen_elements: str
    screenshot_b64: str
    _locator_map: dict
    _element_coords_map: dict
    _prev_locators: list[str]
    ui_delta_summary: str

    findings: list[dict]

    step_routed: bool

    installed_apps: list[str]
    shortcuts_raw: str
    browser_launched: bool
    _opened_app_name: str

    session_context: str

    result: str
    error: str
