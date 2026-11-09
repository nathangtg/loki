from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any

from src.skills.applescript_utils import escape_as_string, run_applescript
from src.skills.base import Skill, SkillIntent
from src.skills.finder_skill import spotlight_locate_paths
from src.skills.registry import register_skill
from src.skills.shell_utils import human_size

LOGGER = logging.getLogger(__name__)


def _resolve_document_path(raw_path: str, kind: str = "") -> Path:
    candidate = Path((raw_path or "").strip()).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    for base in (Path.home() / "Documents", Path.home() / "Downloads", Path.home() / "Desktop"):
        p = base / raw_path.strip()
        if p.is_file():
            return p.resolve()

    matches = spotlight_locate_paths(raw_path, kind=kind, limit=3)
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Could not find document matching '{raw_path}'")


def _extract_pdf_with_quartz(pdf_path: Path, max_pages: int = 10) -> tuple[int, str, dict[str, str]]:
    try:
        import Quartz
        from Foundation import NSURL

        url = NSURL.fileURLWithPath_(str(pdf_path))
        doc = Quartz.PDFDocument.alloc().initWithURL_(url)
        if doc is None:
            raise RuntimeError("Quartz.PDFDocument failed to open file")

        page_count = int(doc.pageCount())
        attrs = doc.documentAttributes() or {}
        meta = {
            "title": str(attrs.get("Title") or pdf_path.stem),
            "author": str(attrs.get("Author") or "Unknown"),
            "pages": str(page_count),
        }

        pages_text: list[str] = []
        limit = min(page_count, max(1, max_pages))
        for idx in range(limit):
            page = doc.pageAtIndex_(idx)
            if page is not None:
                txt = str(page.string() or "").strip()
                if txt:
                    pages_text.append(f"[Page {idx + 1}]\n{txt}")
        return page_count, "\n\n".join(pages_text), meta
    except Exception as exc:
        LOGGER.debug("documents_skill: Quartz PDF extraction fallback: %s", exc)
        from src.skills.shell_utils import _pdf_snippet

        text = _pdf_snippet(pdf_path, max_chars=6000)
        return 1, text, {"title": pdf_path.stem, "author": "Unknown", "pages": "?"}


def _read_pdf_text(params: dict[str, Any]) -> str:
    raw_path = (params.get("path") or params.get("query") or "").strip()
    if not raw_path:
        raise ValueError("Missing 'path' parameter for read_pdf_text")
    try:
        max_pages = max(1, min(30, int(params.get("max_pages") or 5)))
    except (ValueError, TypeError):
        max_pages = 5

    pdf_path = _resolve_document_path(raw_path, kind="pdf")
    page_count, text, meta = _extract_pdf_with_quartz(pdf_path, max_pages=max_pages)
    if not text:
        return f"Opened '{pdf_path.name}' ({page_count} pages), but no selectable text was found."
    truncated = text[:4000]
    return (
        f"PDF: {pdf_path.name} ({meta.get('pages', page_count)} pages, Title: {meta.get('title')})\n"
        f"Path: {pdf_path}\n\n"
        f"{truncated}"
    )


def _pdf_metadata(params: dict[str, Any]) -> str:
    raw_path = (params.get("path") or "").strip()
    if not raw_path:
        raise ValueError("Missing 'path' parameter for pdf_metadata")
    pdf_path = _resolve_document_path(raw_path, kind="pdf")
    page_count, _, meta = _extract_pdf_with_quartz(pdf_path, max_pages=1)
    size_str = human_size(pdf_path.stat().st_size)
    return (
        f"PDF Metadata for '{pdf_path.name}':\n"
        f"- Path: {pdf_path}\n"
        f"- Pages: {page_count}\n"
        f"- Title: {meta.get('title', pdf_path.stem)}\n"
        f"- Author: {meta.get('author', 'Unknown')}\n"
        f"- Size: {size_str}"
    )


def _create_pages_document(params: dict[str, Any]) -> str:
    title = (params.get("title") or "Untitled Document").strip()
    body = (params.get("body") or "").strip()
    safe_title = escape_as_string(title)
    safe_body = escape_as_string(f"{title}\n\n{body}" if body else title)

    for app_candidate in ("Pages Creator Studio", "Pages"):
        script = f'''
        tell application "{app_candidate}"
            activate
            set newDoc to make new document with properties {{body text:"{safe_body}"}}
            return name of newDoc
        end tell
        '''
        try:
            doc_name = run_applescript(script, timeout=15.0)
            return f"Created document '{doc_name or safe_title}' in {app_candidate}."
        except Exception:
            continue
    raise RuntimeError("Could not create document in Pages.")


def _read_excel_sheet(params: dict[str, Any]) -> str:
    raw_path = (params.get("path") or "").strip()
    open_block = ""
    if raw_path:
        resolved = _resolve_document_path(raw_path, kind="spreadsheet")
        safe_posix = escape_as_string(str(resolved))
        open_block = f'open POSIX file "{safe_posix}"\n'

    script = f'''
    tell application "Microsoft Excel"
        {open_block}if (count of workbooks) is 0 then
            return "NO_WORKBOOK"
        end if
        set usedVals to string value of used range of active sheet of active workbook
        set outputLines to ""
        set rowCount to count of usedVals
        if rowCount > 15 then set rowCount to 15
        repeat with r from 1 to rowCount
            set rowItem to item r of usedVals
            set rowStr to ""
            if class of rowItem is list then
                repeat with c from 1 to (count of rowItem)
                    if c > 1 then set rowStr to rowStr & " | "
                    set rowStr to rowStr & (item c of rowItem as string)
                end repeat
            else
                set rowStr to rowItem as string
            end if
            set outputLines to outputLines & rowStr & linefeed
        end repeat
        return outputLines
    end tell
    '''
    raw = run_applescript(script, timeout=15.0)
    if raw == "NO_WORKBOOK":
        return "No active workbook is open in Microsoft Excel."
    return f"Active Excel Sheet Data:\n{raw}" if raw else "Active Excel sheet is empty."


def _write_excel_cells(params: dict[str, Any]) -> str:
    values_csv = (params.get("values_csv") or "").strip()
    if not values_csv:
        raise ValueError("Missing 'values_csv' parameter")

    reader = csv.reader(io.StringIO(values_csv.replace(";", "\n")))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError("No valid cell values parsed from values_csv")

    set_statements: list[str] = []
    for r_idx, row in enumerate(rows[:25], start=1):
        for c_idx, val in enumerate(row[:15], start=1):
            col_letter = chr(ord("A") + c_idx - 1)
            cell_ref = f"{col_letter}{r_idx}"
            safe_val = escape_as_string(val.strip())
            set_statements.append(f'set value of cell "{cell_ref}" of active sheet to "{safe_val}"')

    stmts_block = "\n        ".join(set_statements)
    script = f'''
    tell application "Microsoft Excel"
        activate
        if (count of workbooks) is 0 then
            make new workbook
        end if
        {stmts_block}
    end tell
    '''
    run_applescript(script, timeout=15.0)
    return f"Wrote {len(rows)} row(s) ({len(set_statements)} cells) to Microsoft Excel."


def _create_keynote_slide(params: dict[str, Any]) -> str:
    title = escape_as_string((params.get("title") or "Slide Title").strip())
    body = escape_as_string((params.get("body") or "").strip())

    for app_candidate in ("Keynote Creator Studio", "Keynote"):
        script = f'''
        tell application "{app_candidate}"
            activate
            if (count of documents) is 0 then
                set theDoc to make new document
            else
                set theDoc to front document
            end if
            tell theDoc
                set newSlide to make new slide
                try
                    set object text of default title item of newSlide to "{title}"
                end try
                try
                    set object text of default body item of newSlide to "{body}"
                end try
            end tell
        end tell
        '''
        try:
            run_applescript(script, timeout=15.0)
            return f"Added slide '{params.get('title', 'Slide Title')}' in {app_candidate}."
        except Exception:
            continue
    raise RuntimeError("Could not create slide in Keynote.")


_documents_skill = Skill(
    name="documents",
    description=(
        "Read and extract text/metadata from PDFs via native Quartz PDFKit, "
        "and automate Microsoft Excel, Apple Pages, and Apple Keynote documents"
    ),
    app_names={
        "preview",
        "preview.app",
        "pdf",
        "microsoft excel",
        "excel",
        "pages",
        "pages creator studio",
        "numbers",
        "numbers creator studio",
        "keynote",
        "keynote creator studio",
    },
)

_documents_skill.add_intent(
    SkillIntent(
        name="read_pdf_text",
        description="Extract text and metadata from a PDF file by path or partial filename (via Spotlight + Quartz PDFKit)",
        params=["path"],
        handler=_read_pdf_text,
    )
)
_documents_skill.add_intent(
    SkillIntent(
        name="pdf_metadata",
        description="Get page count, title, author, and file size of a PDF document",
        params=["path"],
        handler=_pdf_metadata,
    )
)
_documents_skill.add_intent(
    SkillIntent(
        name="create_pages_document",
        description="Create a new document in Apple Pages with a title and body text",
        params=["title", "body"],
        handler=_create_pages_document,
    )
)
_documents_skill.add_intent(
    SkillIntent(
        name="read_excel_sheet",
        description="Read the used cell range of the active Microsoft Excel worksheet",
        params=[],
        handler=_read_excel_sheet,
    )
)
_documents_skill.add_intent(
    SkillIntent(
        name="write_excel_cells",
        description="Write comma-separated rows of values (rows separated by ';' or newline) into the active Microsoft Excel worksheet",
        params=["values_csv"],
        handler=_write_excel_cells,
    )
)
_documents_skill.add_intent(
    SkillIntent(
        name="create_keynote_slide",
        description="Create a new slide with a title and body text in Apple Keynote",
        params=["title", "body"],
        handler=_create_keynote_slide,
    )
)

register_skill(_documents_skill)
