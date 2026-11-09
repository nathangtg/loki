from __future__ import annotations

import mimetypes
import shutil
from datetime import datetime
from pathlib import Path


def list_directory(path: str) -> list[dict]:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    entries: list[dict] = []
    for item in sorted(target.iterdir()):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        mime, _ = mimetypes.guess_type(item.name)
        entries.append({
            "name": item.name,
            "path": str(item),
            "size_bytes": stat.st_size,
            "modified_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "mime_type": mime or "application/octet-stream",
        })
    return entries


def read_file_snippet(path: str, max_bytes: int = 512) -> str:
    fpath = Path(path).expanduser().resolve()
    if not fpath.is_file():
        return ""

    suffix = fpath.suffix.lower()

    if suffix == ".pdf":
        snippet = _pdf_snippet(fpath, max_chars=max_bytes)
        if snippet:
            return snippet

    try:
        raw = fpath.read_bytes()[:max_bytes]
        return raw.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _pdf_snippet(path: Path, max_chars: int) -> str:
    try:
        from pdfminer.high_level import extract_text_to_fp
        import io

        buf = io.StringIO()
        with open(path, "rb") as f:
            extract_text_to_fp(f, buf, maxpages=1)
        text = buf.getvalue().strip()
        return text[:max_chars] if text else ""
    except Exception:
        return ""


def move_file(src: str, dst_dir: str) -> str:
    source = Path(src).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {src}")

    dest_dir = Path(dst_dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / source.name
    if dest.exists():
        stem, suffix = source.stem, source.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        shutil.move(str(source), str(dest))
    except Exception as exc:
        raise RuntimeError(f"Failed to move '{src}' to '{dst_dir}': {exc}") from exc

    return str(dest)


def human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.0f} TB"
