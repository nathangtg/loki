# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-09

First public release.

### Added

- LangGraph orchestration server with an HTTP API (`/assist`, `/health`, voice session endpoints).
- Agent loop: plan, skill/route, observe, reason, act, reflect, finish.
- 15 built-in skills with 68 intents (Mail, Calendar, Notes, Reminders, Finder, File Organizer, Documents, Shortcuts, Spotify, Music, Messages, Contacts, Meetings, System Control, Web Search), plus skills generated at runtime from application AppleScript dictionaries.
- Accessibility-tree observation through batched `HIServices` calls and the Chrome DevTools Protocol, with OCR fallback.
- Local LLM inference through `llama-cpp-python` (grammar-constrained JSON actions, prompt caching, prompt-lookup speculative decoding) with an optional `mlx-lm` backend.
- Embedding-based ranking of interface elements and optional prompt compression with LLMLingua.
- Streaming speech-to-text with Moonshine and text-to-speech with Kokoro-82M (4-bit MLX), with the macOS `say` command as a fallback. Model files are verified against checksums in `orchestration/model-manifests/`.
- Login playbook recording and replay, with passwords stored in the macOS Keychain.
- Native macOS SwiftUI app: floating orb, text bar, global shortcuts, Settings window, VoiceOver announcements, launch at login, and a packaged `Loki.app` (`make app`).
- Makefile targets for setup, model download and verification, and running the server and app.

### Known issues

- `scripts/calibrate.py` and `mapper_tools.py` use different `calibration.json` locations.
- The Swift interface has no automated tests.
- macOS only.
