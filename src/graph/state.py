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
    token_budget_used: int
    last_compressed_at: Optional[str]


class ATSState(TypedDict):
    """State for ATS sub-workflow candidate ranking."""

    messages: Annotated[list, add_messages]
    client_id: str
    position_id: str
    candidates: List[Dict[str, Any]]
    scores: Dict[str, Dict[str, Any]]
    ranked_candidates: List[Dict[str, Any]]
    report: Optional[str]


class CandidateScreeningState(TypedDict, total=False):
    """Durable state for the bounded Candidate Screening workflow."""

    session_id: str
    client_id: str
    position_id: str
    resume_path: Optional[str]
    linkedin_url: str
    website_url: str
    rubric: Dict[str, Any]
    evidence: Dict[str, Any]
    evidence_warnings: List[str]
    candidate_name: str
    candidate_email: str
    candidate_id: str
    existing_evaluation: Dict[str, Any]
    structured_scores: Dict[str, Any]
    evaluation_submitted: Dict[str, Any]
    memory_written: Dict[str, Any]
    final_response: str
    condition: str
    graph_node: str
    terminal_status: str
