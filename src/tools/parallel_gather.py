"""Parallel candidate data gathering meta-tool."""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel

from src.tools._compat import tool
from src.tools.linkedin_fetcher import fetch_linkedin
from src.tools.resume_parser import parse_resume
from src.tools.website_scraper import scrape_website


class ParallelGatherInput(BaseModel):
    """Input schema for parallel_gather_candidate_info."""

    resume_path: str
    linkedin_url: str
    website_url: Optional[str] = None


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)


@tool(args_schema=ParallelGatherInput)
def parallel_gather_candidate_info(
    resume_path: str,
    linkedin_url: str,
    website_url: Optional[str] = None,
) -> dict:
    """Fetch resume, LinkedIn, and optional website concurrently."""

    async def _gather():
        tasks = [
            asyncio.to_thread(parse_resume.invoke, {"file_path": resume_path}),
            asyncio.to_thread(fetch_linkedin.invoke, {"url": linkedin_url}),
        ]
        if website_url:
            tasks.append(asyncio.to_thread(scrape_website.invoke, {"url": website_url}))
        return await asyncio.gather(*tasks, return_exceptions=True)

    raw = _run_async(_gather())
    website_result = "N/A"
    if website_url:
        if len(raw) > 2 and not isinstance(raw[2], Exception):
            website_result = raw[2]
        elif len(raw) > 2:
            website_result = {"error": str(raw[2])}

    return {
        "resume": raw[0] if not isinstance(raw[0], Exception) else {"error": str(raw[0])},
        "linkedin": raw[1] if not isinstance(raw[1], Exception) else {"error": str(raw[1])},
        "website": website_result,
    }
