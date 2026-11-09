from __future__ import annotations

import argparse
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

_env_file = Path(__file__).resolve().parent.parent / ".env"
if not _env_file.exists():
    _env_file = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_file, override=False)

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from src.graph.config import LLM_BACKEND, MODEL_PATH, MLX_MODEL_PATH
from src.server.config import LOGGER
from src.server.handler import LokiHandler
from src.server.warmup import warmup


def main():
    parser = argparse.ArgumentParser(description="Loki orchestration server")
    parser.add_argument("--port", type=int, default=9820)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), LokiHandler)
    LOGGER.info("Loki orchestration server running on http://127.0.0.1:%d", args.port)
    LOGGER.info("LLM backend: %s | model: %s", LLM_BACKEND, MLX_MODEL_PATH if LLM_BACKEND == "mlx" else MODEL_PATH or "(LOKI_MODEL_PATH not set)")
    LOGGER.info("POST /assist  — run pipeline")
    LOGGER.info("GET  /health  — health check")
    LOGGER.info("POST /voice/session                  — start a voice session")
    LOGGER.info("POST /voice/session/<id>/audio        — push PCM audio (?seq=&final=)")
    LOGGER.info("GET  /voice/session/<id>/events       — poll session events (?after=)")
    LOGGER.info("GET  /voice/session/<id>/audio        — pull synthesized PCM (?after=&limit=)")
    LOGGER.info("POST /voice/session/<id>/reply        — answer a pending question")
    LOGGER.info("POST /voice/session/<id>/cancel       — cancel a session")

    threading.Thread(target=warmup, daemon=True, name="loki-warmup").start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
