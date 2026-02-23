"""State types for the recruiter and ATS workflows."""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class RecruiterState(TypedDict):
    """Primary state for the main recruiter ReAct agent."""

    messages: Annotated[list, add_messages]
    client_id: str
    session_id: str
    position_id: Optional[str]
    current_candidate_id: Optional[str]
    evaluation_complete: bool


class ATSState(TypedDict):
    """State for ATS sub-workflow candidate ranking."""

    messages: Annotated[list, add_messages]
    client_id: str
    position_id: str
    candidates: List[Dict[str, Any]]
    scores: Dict[str, Dict[str, Any]]
    ranked_candidates: List[Dict[str, Any]]
    report: Optional[str]
