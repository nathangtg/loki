from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

_KNOWN_SITES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "known_sites.json"
_KEYCHAIN_SERVICE_PREFIX = "loki.credentials."


def _set_keychain_password(domain: str, username: str, password: str) -> bool:
    if platform.system() != "Darwin" or not domain or not password:
        return False
    acct = username or "default"
    service = f"{_KEYCHAIN_SERVICE_PREFIX}{domain}"
    try:
        res = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-s",
                service,
                "-a",
                acct,
                "-w",
                password,
            ],
            capture_output=True,
            text=True,
            timeout=3.0,
            shell=False,
        )
        return res.returncode == 0
    except Exception as exc:
        LOGGER.debug("Keychain store failed for %s: %s", domain, exc)
        return False


def _get_keychain_password(domain: str, username: str = "") -> str | None:
    if platform.system() != "Darwin" or not domain:
        return None
    acct = username or "default"
    service = f"{_KEYCHAIN_SERVICE_PREFIX}{domain}"
    queries = [
        ["/usr/bin/security", "find-generic-password", "-s", service, "-a", acct, "-w"],
        ["/usr/bin/security", "find-internet-password", "-s", domain, "-a", acct, "-w"],
    ]
    if not username:
        queries.append(["/usr/bin/security", "find-internet-password", "-s", domain, "-w"])

    for cmd in queries:
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3.0,
                shell=False,
            )
            if res.returncode == 0:
                secret = (res.stdout or "").rstrip("\r\n")
                if secret:
                    return secret
        except Exception as exc:
            LOGGER.debug("Keychain lookup failed for %s: %s", domain, exc)
    return None

_LEARN_SYSTEM = """\
You are a memory system for a browser automation agent. You analyze completed \
action histories and extract reusable knowledge to store for future sessions.

Given the agent's action history, current known_sites.json, and the goal, \
output a JSON object with updates to merge into known_sites.json.

Output ONLY a JSON object with these optional fields:
{
  "sites": {"site name": "https://url"},
  "credentials": {
    "domain.com": {
      "username": "actual_value_typed",
      "password": "actual_value_typed"
    }
  },
  "navigation": {
    "domain.com": {
      "Destination Page": ["Parent Menu 1", "Parent Menu 2"]
    }
  }
}

Rules:
- Only include fields you can confidently extract from the actions.
- For credentials: look for type_text actions into login forms.
- For navigation: look for click sequences that expand menus before reaching a destination.
- For sites: extract site_name -> URL mappings from the goal and URLs visited.
- Use the domain from the URL.
- Do not include data that is already correctly stored in known_sites.json.
- Output ONLY the JSON. No prose.
- If nothing new to learn, output: {}"""


class SiteKnowledge:

    _instance: SiteKnowledge | None = None

    _NATIVE_APPS = frozenset({
        "finder", "notes", "terminal", "system settings", "system preferences",
        "calendar", "reminders", "mail", "contacts", "safari", "preview",
        "messages", "facetime", "photos", "music", "tv", "podcasts", "news",
        "stocks", "maps", "weather", "clock", "calculator", "shortcuts",
        "activity monitor", "disk utility", "keychain access", "console",
        "automator", "script editor", "font book", "digital color meter",
        "spotlight", "siri", "launchpad", "mission control", "time machine",
        "vs code", "visual studio code", "xcode", "iterm", "iterm2",
        "spotify", "slack", "discord", "zoom", "teams", "microsoft teams",
        "microsoft edge", "google chrome", "firefox", "arc", "brave",
        "microsoft word", "microsoft excel", "microsoft powerpoint",
        "microsoft outlook", "notion", "obsidian", "sublime text", "textedit",
    })

    def __new__(cls) -> SiteKnowledge:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._data: dict[str, Any] = {}
        self._vs: Any = None
        self._load()

    def _load(self) -> None:
        if _KNOWN_SITES_FILE.exists():
            try:
                with open(_KNOWN_SITES_FILE, "r") as f:
                    self._data = json.load(f)
                LOGGER.debug(
                    "Loaded known_sites.json (%d bytes)",
                    _KNOWN_SITES_FILE.stat().st_size,
                )
            except Exception as exc:
                LOGGER.warning("Failed to load known_sites.json: %s", exc)
                self._data = {}
        else:
            self._data = {"sites": {}, "credentials": {}, "navigation": {}}

    def _get_vector_store(self) -> Any:
        if self._vs is None:
            try:
                from src.graph.vector_store import VectorStore
                self._vs = VectorStore()
            except Exception as exc:
                LOGGER.debug("VectorStore init failed: %s", exc)
        return self._vs

    def sync_vectors(self) -> int:
        vs = self._get_vector_store()
        if vs is None:
            return 0
        return vs.sync_known_sites(self._data)

    def _save(self) -> None:
        try:
            with open(_KNOWN_SITES_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
            LOGGER.info("Saved known_sites.json")
            self.sync_vectors()
        except Exception as exc:
            LOGGER.warning("Failed to save known_sites.json: %s", exc)

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc or ""
        except Exception:
            return ""

    @staticmethod
    def _parent_domain(domain: str) -> str:
        parts = domain.split(".")
        if len(parts) > 2:
            return ".".join(parts[1:])
        return domain

    def _find_credentials(self, domain: str) -> dict | None:
        creds = self._data.get("credentials", {})
        if domain in creds:
            return creds[domain]

        parent = self._parent_domain(domain)
        if parent in creds:
            return creds[parent]

        for stored_domain, stored_creds in creds.items():
            if self._parent_domain(stored_domain) == parent:
                LOGGER.info("Using sibling credentials: %s -> %s", stored_domain, domain)
                return stored_creds

        return None

    def get_credential_context(self, url: str, elements_str: str = "") -> str:
        if not url:
            return ""

        domain = self._extract_domain(url)
        if not domain:
            return ""

        creds = self._find_credentials(domain)
        if not creds:
            return ""

        elements_lower = elements_str.lower()
        has_login_form = any(
            kw in elements_lower
            for kw in (
                "password", "username", "userid", "user_id",
                "email", "login", "sign in", "signin", "log in",
            )
        )
        if not has_login_form:
            return ""

        parts = ["SITE CREDENTIALS (use these EXACT values for the login form):"]
        if creds.get("username"):
            parts.append(f"  Username / ID: {creds['username']}")
        has_password = bool(
            creds.get("keychain")
            or creds.get("password")
            or _get_keychain_password(domain, creds.get("username", ""))
        )
        if has_password:
            parts.append("  Password: {{password}}")
        elif creds.get("username"):
            parts.append("  Password: (not stored — check SSO or ask user)")

        parts.append("  → Type these into the matching input fields using their [N] index.")
        return "\n".join(parts)

    def resolve_password_for_domain(self, url_or_goal: str) -> str | None:
        domain = self._extract_domain(url_or_goal)
        if not domain:
            resolved_url = self.resolve_site_url(url_or_goal)
            if resolved_url:
                domain = self._extract_domain(resolved_url)
        if not domain:
            return None
        creds = self._find_credentials(domain) or {}
        username = creds.get("username", "")
        for candidate_domain in (domain, self._parent_domain(domain)):
            secret = _get_keychain_password(candidate_domain, username)
            if secret:
                return secret
        for stored_domain, stored_creds in self._data.get("credentials", {}).items():
            if self._parent_domain(stored_domain) == self._parent_domain(domain):
                secret = _get_keychain_password(stored_domain, stored_creds.get("username", username))
                if secret:
                    return secret
                if stored_creds.get("password") and stored_creds["password"] != "{{password}}":
                    return stored_creds["password"]
        raw_pw = creds.get("password")
        if raw_pw and raw_pw != "{{password}}":
            return raw_pw
        return None

    def resolve_credentials_for_domain(self, domain_or_url: str) -> dict[str, str]:
        domain = self._extract_domain(domain_or_url) or domain_or_url.strip()
        if not domain:
            return {}
        creds = self._find_credentials(domain) or {}
        username = str(creds.get("username") or "").strip()
        password = self.resolve_password_for_domain(f"https://{domain}/") or ""
        out: dict[str, str] = {}
        if username:
            out["username"] = username
        if password:
            out["password"] = password
        return out

    def resolve_site_url(self, goal: str) -> str | None:
        sites = self._data.get("sites", {})
        if not sites:
            return None
        goal_lower = goal.lower()

        sorted_names = sorted(sites.keys(), key=len, reverse=True)
        for name in sorted_names:
            if name.lower() in goal_lower:
                url = sites[name]
                LOGGER.info("Resolved '%s' -> %s from known_sites (exact)", name, url)
                return url

        goal_words = set(re.findall(r"[a-z0-9]+", goal_lower))
        best_name: str | None = None
        best_score = 0.0
        for name in sorted_names:
            name_words = set(re.findall(r"[a-z0-9]+", name.lower()))
            if not name_words:
                continue
            overlap = len(name_words & goal_words)
            score = overlap / len(name_words)
            if score > best_score:
                best_score = score
                best_name = name

        if best_name and best_score >= 0.5:
            url = sites[best_name]
            LOGGER.info(
                "Resolved '%s' -> %s from known_sites (word match %.0f%%)",
                best_name,
                url,
                best_score * 100,
            )
            return url

        vs = self._get_vector_store()
        if vs:
            try:
                results = vs.search(goal, category="site", top_k=1, threshold=0.40)
                if results:
                    hit = results[0]
                    url = hit["metadata"].get("url", "")
                    name = hit["metadata"].get("name", hit["text"])
                    LOGGER.info(
                        "Resolved '%s' -> %s from VectorStore (sim=%.3f)",
                        name,
                        url,
                        hit["score"],
                    )
                    return url
            except Exception as exc:
                LOGGER.debug("VectorStore search failed: %s", exc)

        return None

    def learn_from_session(
        self, goal: str, history: list[dict], current_url: str = ""
    ) -> None:
        if not history:
            return

        action_lines = []
        for i, h in enumerate(history):
            parts = [f"{i + 1}. {h.get('action', '?')}"]
            if h.get("target"):
                parts.append(f" target={h['target'][:80]}")
            if h.get("selector"):
                parts.append(f" selector={h['selector']}")
            if h.get("text"):
                parts.append(f" text='{h['text']}'")
            if h.get("url"):
                parts.append(f" url={h['url'][:120]}")
            result = h.get("result", "")[:100]
            parts.append(f" → {result}")
            action_lines.append("".join(parts))

        history_text = "\n".join(action_lines)
        existing_json = json.dumps(self._data, indent=2)
        user_prompt = (
            f"GOAL: {goal}\n\n"
            f"CURRENT URL: {current_url}\n\n"
            f"ACTION HISTORY:\n{history_text}\n\n"
            f"CURRENT known_sites.json:\n{existing_json}\n\n"
            "Analyze the actions above. Extract any NEW knowledge to add:\n"
            "- Site names -> URLs visited\n"
            "- Credentials used (username/password typed into login forms)\n"
            "- Navigation paths (menu sequences to reach pages)\n"
            "Output ONLY the JSON updates to merge. Output {} if nothing new."
        )

        try:
            from src.graph.grammars import GENERIC_JSON_OBJECT_GRAMMAR, load_grammar
            from src.graph.llm import call_llm, extract_json

            raw = call_llm(
                _LEARN_SYSTEM,
                user_prompt,
                num_predict=384,
                grammar=load_grammar(GENERIC_JSON_OBJECT_GRAMMAR),
            )
            LOGGER.info("SiteKnowledge LLM learn raw: %s", raw[:300])

            updates = extract_json(raw)
            if not updates:
                LOGGER.info("SiteKnowledge: LLM found nothing new to learn")
                return

            self._apply_updates(updates)
        except Exception as exc:
            LOGGER.warning("SiteKnowledge LLM learning failed: %s", exc)

    @staticmethod
    def _is_valid_site_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            domain = parsed.netloc
            if not domain or "." not in domain:
                return False
            if any(x in domain for x in ("example.", "localhost", "test.", "fake.")):
                return False
            return True
        except Exception:
            return False

    def _apply_updates(self, updates: dict) -> None:
        changed = False

        new_sites = updates.get("sites", {})
        if new_sites:
            existing_sites = self._data.setdefault("sites", {})
            for name, url in new_sites.items():
                name_lower = name.lower().strip()
                if name_lower in self._NATIVE_APPS:
                    LOGGER.info("Rejected site '%s' — native app", name_lower)
                    continue
                if not self._is_valid_site_url(url):
                    LOGGER.info("Rejected site '%s' -> %s — invalid URL", name_lower, url)
                    continue
                if name_lower not in existing_sites:
                    existing_sites[name_lower] = url
                    LOGGER.info("LLM learned site: '%s' -> %s", name_lower, url)
                    changed = True

        new_creds = updates.get("credentials", {})
        if new_creds:
            existing_creds = self._data.setdefault("credentials", {})
            for domain, cred_data in new_creds.items():
                existing = existing_creds.get(domain, {})
                if cred_data.get("username") and not cred_data["username"].startswith("{{"):
                    if existing.get("username") != cred_data["username"]:
                        existing["username"] = cred_data["username"]
                        changed = True
                raw_pw = cred_data.get("password", "")
                if raw_pw and not raw_pw.startswith("{{") and raw_pw != "••••••••":
                    acct = existing.get("username") or cred_data.get("username", "")
                    if _set_keychain_password(domain, acct, raw_pw):
                        existing.pop("password", None)
                        existing["keychain"] = True
                        changed = True
                if changed:
                    existing["last_login"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    existing_creds[domain] = existing
                    LOGGER.info("LLM learned credentials metadata for %s", domain)

        new_nav = updates.get("navigation", {})
        if new_nav:
            existing_nav = self._data.setdefault("navigation", {})
            for domain, paths in new_nav.items():
                domain_nav = existing_nav.setdefault(domain, {})
                for dest, parents in paths.items():
                    if dest not in domain_nav:
                        domain_nav[dest] = parents
                        LOGGER.info("LLM learned navigation: %s -> %s -> %s", domain, parents, dest)
                        changed = True

        if changed:
            self._save()
            LOGGER.info("SiteKnowledge: merged updates into known_sites.json")

    def build_context(self, goal: str, top_k: int = 5) -> str:
        vs = self._get_vector_store()
        if vs:
            try:
                return vs.build_context(goal, top_k=top_k)
            except Exception as exc:
                LOGGER.debug("build_context failed: %s", exc)
        return ""

