"""Resume parsing tool for PDF and DOCX files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Tuple

from pydantic import BaseModel

from src.tools._compat import tool


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

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text, pages = _parse_pdf(path)
        elif suffix == ".docx":
            text, pages = _parse_docx(path)
        else:
            raise ValueError(f"Unsupported file type: {path.suffix}")

        return {"text": text, "hash": file_hash, "pages": pages}
    except Exception as exc:
        return {"error": str(exc), "file_path": file_path}

