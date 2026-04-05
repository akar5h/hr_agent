"""ATS sub-agent with dedicated ranking tools."""

from __future__ import annotations

from typing import Any, Dict, List

from langchain.agents import create_agent
from pydantic import BaseModel

from src.database.db import get_db
from src.llm import build_chat_model
from src.observability.decorators import traced
from src.prompts.ats import build_ats_system_prompt
from src.tools._compat import tool


class FetchCandidatesInput(BaseModel):
    """Input schema for fetch_candidates_for_position."""

    position_id: str
    client_id: str


class ScoreCandidateInput(BaseModel):
    """Input schema for score_candidate."""

    candidate_id: str
    position_id: str
    client_id: str
    rubric: Dict[str, Any]


class RankCandidatesInput(BaseModel):
    """Input schema for rank_candidates."""

    scores: List[Dict[str, Any]]


class GenerateATSReportInput(BaseModel):
    """Input schema for generate_ats_report."""

    position_id: str
    client_id: str
    ranked_candidates: List[Dict[str, Any]]


def _dimension_to_column(dimension: str) -> str:
    if dimension == "culture_fit":
        return "culture_score"
    return f"{dimension}_score"


@tool(args_schema=FetchCandidatesInput)
def fetch_candidates_for_position(position_id: str, client_id: str) -> List[Dict[str, Any]]:
    """Fetch all candidates who have been evaluated for a given position."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                e.candidate_id,
                c.name,
                e.technical_score,
                e.experience_score,
                e.culture_score,
                e.communication_score,
                e.overall_score
            FROM evaluations e
            JOIN candidates c ON c.id = e.candidate_id
            WHERE e.position_id = %s AND e.client_id = %s
            """,
            (position_id, client_id),
        ).fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "candidate_id": row["candidate_id"],
                "name": row["name"],
                "evaluation_scores": {
                    "technical": row["technical_score"],
                    "experience": row["experience_score"],
                    "culture_fit": row["culture_score"],
                    "communication": row["communication_score"],
                    "overall": row["overall_score"],
                },
            }
        )
    return results


@tool(args_schema=ScoreCandidateInput)
def score_candidate(
    candidate_id: str,
    position_id: str,
    client_id: str,
    rubric: Dict[str, Any],
) -> Dict[str, Any]:
    """Calculate weighted overall score for a candidate based on rubric weights."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                c.name,
                e.technical_score,
                e.experience_score,
                e.culture_score,
                e.communication_score
            FROM evaluations e
            JOIN candidates c ON c.id = e.candidate_id
            WHERE e.candidate_id = %s AND e.position_id = %s AND e.client_id = %s
            ORDER BY e.evaluated_at DESC
            LIMIT 1
            """,
            (candidate_id, position_id, client_id),
        ).fetchone()

    if row is None:
        return {"error": "Candidate evaluation not found", "candidate_id": candidate_id}

    row_dict = dict(row)
    weighted_total = 0.0
    breakdown: Dict[str, Dict[str, float]] = {}
    for dimension, weight_value in rubric.items():
        weight = float(weight_value)
        column = _dimension_to_column(dimension)
        raw_score = float(row_dict.get(column, 0) or 0)
        weighted = raw_score * weight / 100.0
        weighted_total += weighted
        breakdown[dimension] = {
            "raw_score": raw_score,
            "weight": weight,
            "weighted": weighted,
        }

    return {
        "candidate_id": candidate_id,
        "name": row_dict["name"],
        "weighted_score": round(weighted_total, 4),
        "breakdown": breakdown,
    }


@tool(args_schema=RankCandidatesInput)
def rank_candidates(scores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort candidates by weighted score descending. Returns ranked list."""
    sorted_scores = sorted(scores, key=lambda item: float(item.get("weighted_score", 0)), reverse=True)
    ranked: List[Dict[str, Any]] = []
    for index, item in enumerate(sorted_scores, start=1):
        ranked_item = dict(item)
        ranked_item["rank"] = index
        ranked.append(ranked_item)
    return ranked


@tool(args_schema=GenerateATSReportInput)
def generate_ats_report(position_id: str, client_id: str, ranked_candidates: List[Dict[str, Any]]) -> str:
    """Generate a formatted ATS ranking report in markdown."""
    with get_db() as conn:
        position_row = conn.execute(
            "SELECT title FROM positions WHERE id = %s AND client_id = %s",
            (position_id, client_id),
        ).fetchone()

    position_title = position_row["title"] if position_row else position_id
    lines = [
        "# ATS Ranking Report",
        "",
        f"## Position: {position_title} ({position_id})",
        f"## Client: {client_id}",
        "",
        "| Rank | Candidate | Weighted Score |",
        "|---|---|---:|",
    ]
    for candidate in ranked_candidates:
        lines.append(
            f"| {candidate.get('rank', '-')} | {candidate.get('name', 'Unknown')} | "
            f"{float(candidate.get('weighted_score', 0)):.2f} |"
        )

    if ranked_candidates:
        top = ranked_candidates[0]
        lines.extend(
            [
                "",
                "## Recommendation",
                (
                    f"Top candidate: **{top.get('name', 'Unknown')}** "
                    f"(score: {float(top.get('weighted_score', 0)):.2f})."
                ),
            ]
        )
    else:
        lines.extend(["", "## Recommendation", "No evaluated candidates found for ranking."])

    return "\n".join(lines)


ATS_TOOLS = [
    fetch_candidates_for_position,
    score_candidate,
    rank_candidates,
    generate_ats_report,
]


@traced(name="build-ats-agent")
def build_ats_agent(client_id: str, position_id: str, rubric: Dict[str, Any]):
    """Build ATS sub-agent for position-level ranking."""
    model = build_chat_model(temperature=0)

    system_prompt = build_ats_system_prompt(
        client_id=client_id,
        position_id=position_id,
        rubric=rubric,
    )

    return create_agent(
        model=model,
        tools=ATS_TOOLS,
        system_prompt=system_prompt,
    )
