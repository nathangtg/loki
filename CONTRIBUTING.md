# Contributing to LOKI

Bug reports, fixes, documentation and research contributions are welcome. For anything larger than a small fix, open an issue first so the approach can be agreed before you invest time.

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## Setup

You need macOS 13+, [`uv`](https://github.com/astral-sh/uv), and Swift 5.9+ (Xcode Command Line Tools) for the interface.

```bash
git clone https://github.com/<you>/loki.git
cd loki
make setup          # installs orchestration dependencies from uv.lock
cp .env.example .env
make models         # downloads the LLM, TTS and STT models
make dev            # starts the server, then the macOS app
```

See [README.md](README.md) for configuration.

## Checks

Run these before opening a pull request:

```bash
make test
make lint
make build    # only needed if you changed interface/
```

Some tests exercise the Moonshine and Kokoro runtimes and need the models from `make models`.

## Conventions

- Python 3.12, formatted and linted with Ruff (config in `orchestration/pyproject.toml`).
- Comment why, not what. Don't add docstrings that restate the function name.
- Swift targets macOS 13 and uses `@MainActor` for UI state.
- Add or update tests for behavior changes.
- Never commit `.env`, model weights, recorded site data or credentials. `orchestration/data/` is runtime state and is ignored.

## Commits and pull requests

Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat(graph): ...`, `fix(tools): ...`). Keep pull requests focused and fill in the template.

## Research contributions

If you use LOKI in academic work, cite it with [CITATION.cff](CITATION.cff). Evaluation and reproducibility improvements are especially welcome; the benchmark and probe scripts live in `orchestration/scripts/`.

## License

Unless you state otherwise, any contribution you submit is licensed under the [Apache License 2.0](LICENSE), as described in section 5 of that license. It includes a patent grant from you for your contribution.
