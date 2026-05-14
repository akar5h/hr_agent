"""Resume parsing tool for PDF and DOCX files."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Tuple

from pydantic import BaseModel

from src.cache.tool_cache import ToolCache
from src.tools._compat import tool

MAX_RESUME_CHARS = 4000  # ~1000 tokens — key skills + experience are in the first 4K
_CACHE = ToolCache()
UNTRUSTED_RESUME_START = "BEGIN_UNTRUSTED_RESUME_TEXT"
UNTRUSTED_RESUME_END = "END_UNTRUSTED_RESUME_TEXT"

_SUSPICIOUS_INSTRUCTION = re.compile(
    r"("
    r"ignore\s+(all\s+)?(previous|prior)\s+instructions|"
    r"system\s*:|developer\s*:|assistant\s*:|"
    r"recruiter\s+ai\s+system\s+note|"
    r"call\s+(write_database|query_database|store_memory)|"
    r"override\s+(the\s+)?(rubric|scoring|score)|"
    r"always\s+(shortlist|hire|reject|score)"
    r")",
    re.IGNORECASE,
)


class ParseResumeInput(BaseModel):
    """Input schema for parse_resume."""

    file_path: str


def _is_near_white(color: Any) -> bool:
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return float(color) >= 0.98
    if isinstance(color, (tuple, list)) and color:
        numeric = [float(item) for item in color if isinstance(item, (int, float))]
        return bool(numeric) and all(item >= 0.98 for item in numeric)
    return False


def _char_is_visible(page: Any, char: dict[str, Any]) -> bool:
    size = float(char.get("size") or 0)
    if 0 < size < 3:
        return False
    if _is_near_white(char.get("non_stroking_color")):
        return False

    x0 = float(char.get("x0") or 0)
    x1 = float(char.get("x1") or 0)
    top = float(char.get("top") or 0)
    bottom = float(char.get("bottom") or 0)
    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    return x1 >= 0 and x0 <= width and bottom >= 0 and top <= height


def _pdf_hidden_text_warnings(pdf: Any) -> list[str]:
    hidden_count = 0
    total_count = 0
    for page in pdf.pages:
        for char in getattr(page, "chars", []) or []:
            total_count += 1
            if not _char_is_visible(page, char):
                hidden_count += 1

    if hidden_count:
        return [f"Hidden or visually suppressed PDF text removed: {hidden_count}/{total_count} chars."]
    return []


def _parse_pdf(file_path: Path) -> Tuple[str, int, list[str]]:
    import pdfplumber

    with pdfplumber.open(str(file_path)) as pdf:
        warnings = _pdf_hidden_text_warnings(pdf)
        page_text = []
        for page in pdf.pages:
            filtered = page.filter(
                lambda obj: obj.get("object_type") != "char" or _char_is_visible(page, obj)
            )
            page_text.append(filtered.extract_text() or "")
    return "\n".join(page_text), len(page_text), warnings


def _parse_docx(file_path: Path) -> Tuple[str, int, list[str]]:
    from docx import Document

    doc = Document(str(file_path))
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    return text, 1, []


def _sanitize_resume_text(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    sanitized_lines: list[str] = []
    redacted_count = 0

    for line in text.splitlines():
        if _SUSPICIOUS_INSTRUCTION.search(line):
            redacted_count += 1
            sanitized_lines.append("[REDACTED suspicious resume instruction]")
        else:
            sanitized_lines.append(line)

    if redacted_count:
        warnings.append(f"Suspicious instruction-like resume lines redacted: {redacted_count}.")
    return "\n".join(sanitized_lines), warnings


def _wrap_untrusted_resume_text(text: str) -> str:
    return f"{UNTRUSTED_RESUME_START}\n{text}\n{UNTRUSTED_RESUME_END}"


@tool(args_schema=ParseResumeInput)
def parse_resume(file_path: str) -> dict:
    """Parse a resume PDF or DOCX file and return the extracted text content."""
    try:
        path = Path(file_path)
        file_bytes = path.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        cache_key = {"file_hash": file_hash}
        cached = _CACHE.get("parse_resume", cache_key)
        if cached is not None:
            if isinstance(cached, dict):
                return {**cached, "cached": True}
            return {"text": str(cached), "cached": True}

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            parsed = _parse_pdf(path)
        elif suffix == ".docx":
            parsed = _parse_docx(path)
        else:
            raise ValueError(f"Unsupported file type: {path.suffix}")

        if len(parsed) == 2:
            text, pages = parsed
            warnings = []
        else:
            text, pages, warnings = parsed

        text, sanitization_warnings = _sanitize_resume_text(text)
        warnings.extend(sanitization_warnings)

        truncated = False
        if len(text) > MAX_RESUME_CHARS:
            text = text[:MAX_RESUME_CHARS] + "\n[... truncated for context efficiency ...]"
            truncated = True

        result = {
            "text": _wrap_untrusted_resume_text(text),
            "hash": file_hash,
            "pages": pages,
            "truncated": truncated,
            "warnings": warnings,
            "hidden_text_detected": any("Hidden" in warning for warning in warnings),
            "suspicious_instruction_detected": any("Suspicious" in warning for warning in warnings),
        }
        _CACHE.set("parse_resume", cache_key, result, ttl_seconds=24 * 3600)
        return result
    except Exception as exc:
        return {"error": str(exc), "file_path": file_path}
