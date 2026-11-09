from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SkillIntent:
    name: str
    description: str
    params: list[str] = field(default_factory=list)
    handler: Callable[[dict], str] = None


@dataclass
class Skill:
    name: str
    description: str
    app_names: set[str]
    intents: dict[str, SkillIntent] = field(default_factory=dict)

    def add_intent(self, intent: SkillIntent) -> None:
        self.intents[intent.name] = intent

    def matches_app(self, app_name: str) -> bool:
        app_lower = (app_name or "").strip().lower()
        if not app_lower:
            return False
        return any(a == app_lower or a in app_lower or app_lower in a for a in self.app_names)

    def describe(self) -> str:
        lines = [f"SKILL '{self.name}' — {self.description} (apps: {', '.join(sorted(self.app_names))})"]
        for intent in self.intents.values():
            params_str = ", ".join(intent.params) if intent.params else "(none)"
            lines.append(f"  - intent \"{intent.name}\": {intent.description} — params: {params_str}")
        return "\n".join(lines)

    def execute(self, intent_name: str, params: dict) -> str:
        intent = self.intents.get(intent_name)
        if intent is None:
            raise ValueError(f"Skill '{self.name}' has no intent '{intent_name}'")
        missing = [p for p in intent.params if not params.get(p)]
        if missing:
            raise ValueError(f"Missing required params for '{intent_name}': {missing}")
        if intent.handler is None:
            raise RuntimeError(f"Intent '{intent_name}' has no handler wired up")
        return intent.handler(params)
