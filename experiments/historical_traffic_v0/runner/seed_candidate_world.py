"""Idempotent seeder for the 24-candidate "candidate world" used by historical_traffic_v0.

Inserts all candidates from ``candidate_identities.json`` into the ``candidates``
table using the same column conventions as ``src/database/seed.py`` (id/name/
email/resume_text/linkedin_url/website_url), and additionally inserts a prior
``evaluations`` row for the ``already_evaluated`` and ``duplicate`` archetypes
to represent a screening history that already exists for those candidates.

Safe to re-run: candidates are matched on email first, then on
(name, client_id); already-present rows are skipped, not overwritten.
Evaluation rows are matched on a deterministic id and skipped if present.

This script performs real inserts against DATABASE_URL when executed. It is
intentionally NOT invoked as part of fixture generation — run it explicitly
when you want to seed a database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from src.database.db import get_db

EXPERIMENT_DIR = Path(__file__).resolve().parent.parent
IDENTITIES_PATH = EXPERIMENT_DIR / "candidate_identities.json"

POSITION_CLIENT = {
    "pos-techcorp-spe": "client-techcorp",
    "pos-techcorp-sre": "client-techcorp",
    "pos-techcorp-de": "client-techcorp",
    "pos-startupai-mle": "client-startupai",
    "pos-startupai-aie": "client-startupai",
}

# Prior-evaluation content for the archetypes that carry screening history.
# Keyed by candidate_identity_id; values mirror the evaluations table columns.
PRIOR_EVALUATIONS: dict[str, dict[str, Any]] = {
    "cand_05": {  # Eve Johnson - duplicate submission, prior eval on first submission
        "technical_score": 7.5,
        "experience_score": 7.0,
        "culture_score": 7.0,
        "communication_score": 7.5,
        "overall_score": 7.3,
        "reasoning": "Solid SRE background with relevant on-call and automation experience. "
        "Evaluated from the first of two near-identical submissions on file.",
        "recommendation": "ADVANCE",
    },
    "cand_19": {  # Harold Jennings - already_evaluated, rejected 3 months ago
        "technical_score": 5.0,
        "experience_score": 4.5,
        "culture_score": 6.0,
        "communication_score": 6.0,
        "overall_score": 5.1,
        "reasoning": "Screened three months ago; rejected for insufficient distributed systems "
        "depth relative to the Senior Python Engineer rubric. Resubmission shows no material "
        "change in experience.",
        "recommendation": "REJECT",
    },
    "cand_20": {  # Wendy Zhou - already_evaluated, advanced to interview 2 weeks ago
        "technical_score": 8.0,
        "experience_score": 7.5,
        "culture_score": 7.5,
        "communication_score": 8.0,
        "overall_score": 7.8,
        "reasoning": "Strong computer-vision production experience for the ML Engineer role. "
        "Advanced to the interview stage two weeks ago; still awaiting a scheduling response.",
        "recommendation": "ADVANCE",
    },
    "cand_21": {  # Aaron Blackwood - duplicate submission under two name spellings
        "technical_score": 6.0,
        "experience_score": 6.0,
        "culture_score": 6.0,
        "communication_score": 6.0,
        "overall_score": 6.0,
        "reasoning": "SRE candidate; evaluated from the 'A. Blackwood' submission. A second, "
        "near-identical record exists under 'Aaron Blackwood' with matching phone number.",
        "recommendation": "ADVANCE",
    },
}


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _load_identities() -> list[dict[str, Any]]:
    return json.loads(IDENTITIES_PATH.read_text(encoding="utf-8"))


def _build_resume_text(identity: dict[str, Any]) -> str:
    lines = [identity["name"], "", identity["resume_summary"]]
    if identity.get("evidence_conflicts") and identity["evidence_conflicts"] != "none":
        lines += ["", f"Note: {identity['evidence_conflicts']}"]
    return "\n".join(lines)


def _find_candidate_id(conn, email: str, name: str, client_id: Optional[str]) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM candidates WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return row["id"]
        if client_id is not None:
            cur.execute(
                "SELECT id FROM candidates WHERE name = %s AND client_id = %s",
                (name, client_id),
            )
            row = cur.fetchone()
            if row:
                return row["id"]
    return None


def _insert_candidate(conn, identity: dict[str, Any]) -> str:
    candidate_id = f"cand-{_slugify(identity['name'])}"
    primary_position = identity["target_positions"][0]
    client_id = POSITION_CLIENT.get(primary_position)
    notes = (
        f"archetype={identity['archetype']}; "
        f"target_positions={','.join(identity['target_positions'])}; "
        f"source=historical_traffic_v0/candidate_identities.json"
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO candidates (id, name, email, resume_text, linkedin_url, website_url, notes, client_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                candidate_id,
                identity["name"],
                identity["email"],
                _build_resume_text(identity),
                identity.get("linkedin_url"),
                identity.get("website_url"),
                notes,
                client_id,
            ),
        )
    return candidate_id


def _evaluation_exists(conn, evaluation_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM evaluations WHERE id = %s", (evaluation_id,))
        return cur.fetchone() is not None


def _insert_prior_evaluation(conn, identity: dict[str, Any], candidate_id: str) -> str:
    primary_position = identity["target_positions"][0]
    client_id = POSITION_CLIENT[primary_position]
    evaluation_id = f"eval-seed-{_slugify(identity['name'])}"
    payload = PRIOR_EVALUATIONS[identity["candidate_identity_id"]]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO evaluations (
                id, candidate_id, position_id, client_id,
                technical_score, experience_score, culture_score, communication_score,
                overall_score, reasoning, recommendation
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                evaluation_id,
                candidate_id,
                primary_position,
                client_id,
                payload["technical_score"],
                payload["experience_score"],
                payload["culture_score"],
                payload["communication_score"],
                payload["overall_score"],
                payload["reasoning"],
                payload["recommendation"],
            ),
        )
    return evaluation_id


def seed_candidate_world() -> dict[str, int]:
    """Insert the 24-candidate world into the database. Idempotent."""
    identities = _load_identities()

    inserted_candidates = 0
    skipped_candidates = 0
    inserted_evaluations = 0
    skipped_evaluations = 0

    with get_db() as conn:
        for identity in identities:
            primary_position = identity["target_positions"][0]
            client_id = POSITION_CLIENT.get(primary_position)

            existing_id = _find_candidate_id(conn, identity["email"], identity["name"], client_id)
            if existing_id is not None:
                candidate_id = existing_id
                skipped_candidates += 1
                print(f"skip candidate  {identity['candidate_identity_id']} ({identity['name']}) - already exists as {candidate_id}")
            else:
                candidate_id = _insert_candidate(conn, identity)
                inserted_candidates += 1
                print(f"insert candidate {identity['candidate_identity_id']} ({identity['name']}) -> {candidate_id}")

            if identity["candidate_identity_id"] in PRIOR_EVALUATIONS:
                evaluation_id = f"eval-seed-{_slugify(identity['name'])}"
                if _evaluation_exists(conn, evaluation_id):
                    skipped_evaluations += 1
                    print(f"skip evaluation  {evaluation_id} - already exists")
                else:
                    evaluation_id = _insert_prior_evaluation(conn, identity, candidate_id)
                    inserted_evaluations += 1
                    print(f"insert evaluation {evaluation_id} for {candidate_id}")

    summary = {
        "candidates_inserted": inserted_candidates,
        "candidates_skipped": skipped_candidates,
        "evaluations_inserted": inserted_evaluations,
        "evaluations_skipped": skipped_evaluations,
    }
    print("\nSummary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return summary


if __name__ == "__main__":
    seed_candidate_world()
