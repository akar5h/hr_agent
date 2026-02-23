"""Unit tests for resume parsing tool."""

from __future__ import annotations

import hashlib

from src.tools.resume_parser import parse_resume
from tests.unit.tools.utils import invoke_tool


def test_parse_resume_pdf_happy_path(monkeypatch, tmp_path) -> None:
    sample_pdf = tmp_path / "resume.pdf"
    sample_pdf.write_bytes(b"fake-pdf-bytes")

    from src.tools import resume_parser

    monkeypatch.setattr(resume_parser, "_parse_pdf", lambda _: ("PDF text", 2))

    result = invoke_tool(parse_resume, file_path=str(sample_pdf))
    assert result["text"] == "PDF text"
    assert result["pages"] == 2
    assert result["hash"] == hashlib.sha256(b"fake-pdf-bytes").hexdigest()


def test_parse_resume_returns_error_for_missing_file() -> None:
    result = invoke_tool(parse_resume, file_path="/tmp/does-not-exist.pdf")
    assert "error" in result
    assert result["file_path"] == "/tmp/does-not-exist.pdf"


def test_parse_resume_returns_error_for_unsupported_extension(tmp_path) -> None:
    sample = tmp_path / "resume.txt"
    sample.write_text("plain text", encoding="utf-8")

    result = invoke_tool(parse_resume, file_path=str(sample))
    assert "error" in result
    assert "Unsupported file type" in result["error"]

