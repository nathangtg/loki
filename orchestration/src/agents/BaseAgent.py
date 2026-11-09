from __future__ import annotations

import logging

from src.graph.llm import call_llm

LOGGER = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, temperature: float = 0.2, num_predict: int = 2048):
        self.temperature = temperature
        self.num_predict = num_predict

    def _call_llm(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        num_predict: int | None = None,
        grammar: object | None = None,
    ) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        return call_llm(
            system,
            user,
            num_predict=num_predict or self.num_predict,
            temperature=temperature if temperature is not None else self.temperature,
            grammar=grammar,
        )

