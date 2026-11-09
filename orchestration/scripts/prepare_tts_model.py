from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

from src.audio.tts.artifacts import (
    load_artifact_contract,
    resolve_model_path,
    verify_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    contract = load_artifact_contract()
    destination = resolve_model_path(args.destination)
    snapshot_download(
        repo_id=contract.model_id,
        revision=contract.revision,
        local_dir=destination,
        allow_patterns=[artifact.path for artifact in contract.files],
        force_download=args.force,
    )
    errors = verify_artifacts(destination, contract)
    if errors:
        parser.error("; ".join(errors))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
