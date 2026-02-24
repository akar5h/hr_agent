"""Resume parsing tool for PDF and DOCX files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Tuple

from pydantic import BaseModel

from src.tools._compat import tool

MAX_RESUME_CHARS = 4000  # ~1000 tokens — key skills + experience are in the first 4K

# Module-level cache keyed by file hash to avoid re-parsing the same file twice.
_RESULT_CACHE: dict[str, dict] = {}


class ParseResumeInput(BaseModel):
    """Input schema for parse_resume."""

    file_path: str


def _parse_pdf(file_path: Path) -> Tuple[str, int]:
    import pdfplumber

    with pdfplumber.open(str(file_path)) as pdf:
        page_text = [(page.extract_text() or "") for page in pdf.pages]
    return "\n".join(page_text), len(page_text)


def _parse_docx(file_path: Path) -> Tuple[str, int]:
    from docx import Document

    doc = Document(str(file_path))
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    return text, 1


@tool(args_schema=ParseResumeInput)
def parse_resume(file_path: str) -> dict:
    """Parse a resume PDF or DOCX file and return the extracted text content."""
    try:
        path = Path(file_path)
        file_bytes = path.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        if file_hash in _RESULT_CACHE:
            return {**_RESULT_CACHE[file_hash], "cached": True}

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text, pages = _parse_pdf(path)
        elif suffix == ".docx":
            text, pages = _parse_docx(path)
        else:
            raise ValueError(f"Unsupported file type: {path.suffix}")

        truncated = False
        if len(text) > MAX_RESUME_CHARS:
            text = text[:MAX_RESUME_CHARS] + "\n[... truncated for context efficiency ...]"
            truncated = True

        result = {"text": text, "hash": file_hash, "pages": pages, "truncated": truncated}
        _RESULT_CACHE[file_hash] = result
        return result
    except Exception as exc:
        return {"error": str(exc), "file_path": file_path}

