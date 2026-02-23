"""Tool exports for agent registration."""

from src.tools.database_tools import get_hiring_rubric, query_database, write_database
from src.tools.deduplicator import deduplicate_candidate
from src.tools.linkedin_fetcher import fetch_linkedin
from src.tools.memory_tools import retrieve_memory, store_memory
from src.tools.resume_parser import parse_resume
from src.tools.web_search import search_web
from src.tools.website_scraper import scrape_website

ALL_TOOLS = [
    parse_resume,
    fetch_linkedin,
    scrape_website,
    search_web,
    query_database,
    write_database,
    get_hiring_rubric,
    deduplicate_candidate,
    store_memory,
    retrieve_memory,
]

__all__ = ["ALL_TOOLS"]

