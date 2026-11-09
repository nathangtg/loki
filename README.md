<div align="center">

<img src="assets/loki.svg" alt="LOKI logo" width="140" />

# LOKI

**Local Orchestration Kernel Interface**

A local, voice- and text-driven assistant that operates macOS applications through native accessibility APIs.

</div>

LOKI takes a natural-language request, plans it, and carries it out on your Mac. It prefers deterministic routes (AppleScript and Shortcuts "skills", recorded login playbooks) and falls back to reading a live application's accessibility tree and choosing the next UI action with a small local language model. Inference, speech recognition and speech synthesis all run on-device.

LOKI began as a Final Year Project at Sunway University, Malaysia. It is a research prototype: expect rough edges (see [Known limitations](#known-limitations)).

## What it can do

Speak or type a request. LOKI picks a built-in skill when one fits, and otherwise operates the app's interface step by step. These phrases worked in the project's own demo runs (one Mac, Gemma 4 E2B Q4_K_M). Some are matched by fixed rules and the rest by the model, so results can vary:

| Area | Say this | What happens |
|:--|:--|:--|
| Screen | "What is open on my Safari right now?" | Reads the page you are on and your other tabs. |
| Web | "Search up cats on Safari" | Opens the results in Safari. |
| Mail | "How many unread emails do I have?" | Reads the unread count. |
| Mail | "Draft an email to sam@example.com with the subject Friday and the body See you at 3pm" | Opens an unsent draft for you to review. |
| Calendar | "What's on my calendar today?" | Lists today's events. |
| Calendar | "Check my Calendar for a free 30 minute slot tomorrow" | Finds the first free slot. |
| Calendar | "Schedule a meeting called Project Sync tomorrow at 3pm" | Creates a one-hour event. |
| Reminders | "Remind me in Reminders to call mom tomorrow at 9am" | Adds a reminder with a due time. |
| Notes | "Create a note in Notes called Groceries with milk and eggs" | Creates the note. |
| Files | "Search Spotlight for budget" | Lists matching files. |
| Files | "Show me a plan to organize my Downloads folder, but don't move anything" | Proposes a move plan without touching files. |
| Contacts | "What is Sam's email in Contacts?" | Reads the address. |
| Music | "What song is playing on Spotify?" | Reads the current track. Also pause, next and previous, for Spotify and Apple Music. |
| Music | "Play Blinding Lights on Spotify" | Opens Spotify's search for it. You choose the result. |
| System | "Set my Mac's volume to 20 percent" | Sets the output volume. Also mute, Wi-Fi name, clipboard and System Settings panes. |
| Shortcuts | "Run the shortcut named Good Morning" | Runs that Shortcut. |
| Documents | "Read the PDF called resume" | Reads the PDF's text. |

The full set is 15 skills and 68 intents; `orchestration/src/skills/` has the list. Anything no skill matches goes through the slower interface loop.

**Phrasing matters.** A small local model chooses the skill, so the same request can route differently with different wording:

- Name the app: "in Reminders", "in Notes", "in Contacts".
- Use exact titles for notes, reminders and Shortcuts.
- Say "12pm" instead of "noon".
- For file clean-ups, ask for a plan first.

**Mail and iMessage send as soon as the request is understood.** There is no confirmation step. Use your own address or number while you are testing.

## How it works

The orchestration server is a LangGraph state machine:

```
plan → skill / route → observe → reason → act → reflect → finish
```

- **plan** splits the request into steps.
- **skill** and **route** send a step to one of 15 built-in skills (68 intents covering Mail, Calendar, Notes, Reminders, Finder, Spotify, Music, Messages, Contacts, Documents, Shortcuts, system controls and web search) when one applies. Skills for other scriptable apps are generated from their AppleScript dictionaries (`.sdef`).
- **observe** captures the accessibility tree of the frontmost app (Safari and Chromium browsers through the DevTools protocol), with OCR as a fallback for apps that expose little.
- **reason** asks the local LLM for one JSON action (12 types such as `click`, `type_text`, `open_app`), constrained by a grammar so the output is always valid.
- **act** executes the action, **reflect** checks the result, and the loop repeats until the goal is done or fails.

Speech input uses [Moonshine](https://github.com/usefulsensors/moonshine) streaming STT and output uses Kokoro-82M (4-bit MLX), with the macOS `say` command as a fallback. The native SwiftUI app (`interface/loki-macos`) provides the floating orb, the text bar, and the global shortcuts.

| Directory | Contents |
|:--|:--|
| `orchestration/` | Python server: graph, skills, tools, audio. HTTP API on `127.0.0.1:9820`. |
| `interface/loki-macos/` | Swift/SwiftUI menu bar app. |

## Requirements

- macOS 13 or later; Apple Silicon recommended
- [`uv`](https://github.com/astral-sh/uv) and Python 3.12
- Swift 5.9+ (Xcode Command Line Tools)
- About 5 GB of disk for the default models, and a Hugging Face account if you want the embedding model

## Quick start

```bash
make setup               # install Python dependencies from uv.lock
cp .env.example .env
make models              # download the LLM, TTS and STT models
# edit .env: set LOKI_MODEL_PATH and LOKI_MMPROJ_PATH (see below)
make dev                 # start the server, wait for /health, launch the app
```

`make models` downloads Gemma 4 E2B (GGUF) into `models/`. Gemma weights are under Google's [Gemma Terms of Use](https://ai.google.dev/gemma/terms).

**Set `LOKI_MODEL_PATH` and `LOKI_MMPROJ_PATH` in `.env`** to the absolute paths of the downloaded files (`models/gemma-4-E2B-it-Q4_K_M.gguf` and `models/mmproj-BF16.gguf`). The server will not start inference without a model path.

**Optional, recommended: `make embeddings`.** UI elements are ranked with EmbeddingGemma-300M. That model is gated on Hugging Face, so first accept its terms at https://huggingface.co/google/embeddinggemma-300m and run `uv run --project orchestration hf auth login`. Without it LOKI still works, but falls back to keyword-only ranking and picks worse targets.

Other targets: `make server`, `make interface`, `make app` (builds `Loki.app`), `make verify` (checks model files against their manifests). `make help` lists everything.

On first use macOS will ask for Accessibility, Microphone and Automation permissions. LOKI cannot control other apps without them.

Press **⌘⇧V** to speak a command or **⌘⇧Space** to type one.

## Configuration

All settings are environment variables, read from `.env` in the repository root. `.env.example` documents each one. The ones you are most likely to change:

| Variable | Purpose |
|:--|:--|
| `LOKI_MODEL_PATH`, `LOKI_MMPROJ_PATH` | GGUF model and multimodal projector. |
| `LOKI_LLM_BACKEND` | `llamacpp` (default) or `mlx`. The MLX backend needs `mlx-lm`, which is not installed by default. |
| `LOKI_LORA_PATH`, `LOKI_TERSE_SYSTEM` | Use a fine-tuned adapter with a shortened system prompt. |
| `LOKI_SPECULATIVE_MODE` | `prompt_lookup` (default), `draft_model` or `none`. |
| `LOKI_TTS_BACKEND` | `system` or `kokoro`. |

See [`orchestration/README.md`](orchestration/README.md) for the LLM, STT and TTS details and [`interface/README.md`](interface/README.md) for the app.

## Testing

```bash
cd orchestration
uv run --frozen pytest
uv run --frozen ruff check .

cd ../interface/loki-macos
swift build
```

The STT and TTS tests use the Moonshine and Kokoro runtimes and need the models from `make models`. There are no automated tests for the Swift interface.

## Reproducibility

- Benchmark and probe scripts are in `orchestration/scripts/` (for example `evaluate_thesis_ablation.py` and `benchmark_models.py`). Results depend on your hardware and models, so rerun them rather than relying on figures from elsewhere.

## Known limitations

- macOS only. The interface and the accessibility layer are Apple-specific.
- A small local model chooses UI actions, so complex multi-step tasks fail more often than with a hosted model. LOKI has no measured end-to-end success rate yet.
- Applications that expose a poor accessibility tree (some Electron, game and custom-drawn UIs) fall back to OCR, which is less reliable.
- Routing depends on wording (see above). In testing, dark-mode requests toggled the setting even when asked for "on" or "off".
- LOKI acts autonomously with the permissions you grant it. Try it on non-critical tasks first and read [SECURITY.md](SECURITY.md).
- `scripts/calibrate.py` writes `calibration.json` to a different location than `mapper_tools.py` reads, so calibration has no effect.
- The interface has no automated tests.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE). Third-party components and model licences are listed in [NOTICE](NOTICE). To cite LOKI, use [CITATION.cff](CITATION.cff); related work is in [REFERENCES.md](REFERENCES.md).
