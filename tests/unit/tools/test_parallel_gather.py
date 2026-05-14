"""Unit tests for parallel candidate data gathering."""

from __future__ import annotations

from src.tools.parallel_gather import parallel_gather_candidate_info
from tests.unit.tools.utils import invoke_tool


class _DummyTool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self.result


def test_parallel_gather_passes_linkedin_url_key(monkeypatch) -> None:
    from src.tools import parallel_gather

    resume_tool = _DummyTool({"text": "resume"})
    linkedin_tool = _DummyTool({"name": "Alice"})
    website_tool = _DummyTool({"title": "Portfolio"})
    monkeypatch.setattr(parallel_gather, "parse_resume", resume_tool)
    monkeypatch.setattr(parallel_gather, "fetch_linkedin", linkedin_tool)
    monkeypatch.setattr(parallel_gather, "scrape_website", website_tool)

    result = invoke_tool(
        parallel_gather_candidate_info,
        resume_path="/tmp/resume.pdf",
        linkedin_url="https://linkedin.com/in/alice-chen-dev",
        website_url="https://alicechen.dev",
    )

    assert result["resume"] == {"text": "resume"}
    assert result["linkedin"] == {"name": "Alice"}
    assert result["website"] == {"title": "Portfolio"}
    assert linkedin_tool.calls == [{"linkedin_url": "https://linkedin.com/in/alice-chen-dev"}]
