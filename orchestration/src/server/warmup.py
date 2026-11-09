from __future__ import annotations

import os

from src.server.config import LOGGER, TTS_BACKEND


def warmup() -> None:
    _warmup_stt()
    _warmup_system_tts()
    if os.getenv("LOKI_TTS_BACKEND", TTS_BACKEND).strip().lower() == "kokoro":
        _warmup_tts()
    else:
        LOGGER.info("Warmup: skipping Kokoro MLX TTS (using macOS system speech by default to save Unified Memory)")
    _warmup_llm()
    _warmup_vectors()
    _warmup_apps()


def _warmup_stt() -> None:
    LOGGER.info("Warmup: pre-loading STT speculative engine")
    try:
        from src.server.config import get_stt_engine

        engine = get_stt_engine()
        engine.load()
        LOGGER.info("Warmup: STT speculative engine loaded and resident in Unified Memory.")
    except Exception as exc:
        LOGGER.warning("Warmup: STT engine init failed — %s", exc)


def _warmup_system_tts() -> None:
    LOGGER.info("Warmup: initialising macOS system speech fallback")
    try:
        from src.server.config import get_system_tts_adapter

        adapter = get_system_tts_adapter()
        adapter.load()
        LOGGER.info("Warmup: macOS system speech fallback ready.")
    except Exception as exc:
        LOGGER.warning("Warmup: system speech init failed — %s", exc)


def _warmup_tts() -> None:
    LOGGER.info("Warmup: pre-loading Kokoro TTS adapter")
    try:
        from src.server.config import get_tts_adapter

        adapter = get_tts_adapter()
        adapter.load()
        LOGGER.info("Warmup: Kokoro TTS model loaded and resident in Unified Memory.")
    except Exception as exc:
        LOGGER.warning("Warmup: TTS adapter init failed — %s", exc)


def _warmup_llm() -> None:
    LOGGER.info("Warmup: initialising LLM")
    try:
        from src.graph.llm import call_llm

        call_llm("You are a test.", "hi", num_predict=1, temperature=0.0)
        LOGGER.info("Warmup: LLM loaded and resident in-process.")
    except Exception as exc:
        LOGGER.warning("Warmup: LLM init failed — %s", exc)


def _warmup_vectors() -> None:
    LOGGER.info("Warmup: loading EmbeddingGemma-300M …")
    try:
        from src.graph.vector_store import _get_embedding_model, VectorStore

        _get_embedding_model()
        LOGGER.info("Warmup: embedding model loaded")

        from src.graph.site_knowledge import SiteKnowledge

        sk = SiteKnowledge()
        count = sk.sync_vectors()
        vs = VectorStore()
        LOGGER.info("Warmup: Qdrant ready — %d entries (%d new)", vs.count, count)
    except Exception as exc:
        LOGGER.warning("Warmup: VectorStore sync failed — %s", exc)


def _warmup_apps() -> None:
    try:
        from src.graph.app_knowledge import AppKnowledge

        ak = AppKnowledge()
        apps = ak.get_installed_apps()
        LOGGER.info("Warmup: AppKnowledge initialized with %d applications", len(apps))
    except Exception as exc:
        LOGGER.warning("Warmup: AppKnowledge init failed — %s", exc)
