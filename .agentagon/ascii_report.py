"""RESULT.json -> the boxed ASCII release-gate report.

Same format as the committed `data/gate/{codefix,model}.ascii.txt` fixtures in the
agentagon-demo repo — this module was written by reverse-engineering those two files
byte-for-byte, then verified to reproduce them exactly from their source
`{codefix,model}.json` (the same `behavioral-evals-diff-v0.1` schema `run_gate.py`
writes). `run_gate.py` imports `render_ascii_report` from here so the PR-comment output
and the demo UI's pre-baked fixtures are always the same generator, not two hand-synced
copies.

Column widths (id 39, each side 12, 46 dashes) and the trailing "~" marker are fixed to
match those fixtures. Real gates would vary the marker per `finding` (CHANGED/SAME/NEW);
every row in both fixtures is CHANGED, so this generator always prints "~" — a
simplification worth calling out, not a silent guess.
"""

from __future__ import annotations

from typing import Any

_ID_WIDTH = 39
_SIDE_WIDTH = 12
_DASH_WIDTH = 46


def _measured_pct(side: dict[str, Any]) -> str:
    """The measured ("true") share of a boolean measurement, e.g. the share of traces
    that DID loop — one decimal, matching what the agentagon-demo release-diff UI
    renders. Deliberately NOT the largest-share label: for these gates the majority
    label is "false" (not-looping), so a top-label render prints `false 62% -> 70%`,
    which reads as the inverse of the site's `37.5% -> 29.5%` for the same data. The
    PR comment and the UI must show the same number the same way."""
    distribution = side.get("distribution") or {}
    total = sum(distribution.values())
    if total == 0:
        return "n/a"
    measured = distribution.get("true", 0)
    return f"{measured / total * 100:.1f}%"


def render_ascii_report(result: dict[str, Any]) -> str:
    """`result` is a `behavioral-evals-diff-v0.1` bundle (the `RESULT.json` shape
    `run_gate.py` writes, and what `data/gate/*.json` already are)."""
    lines = [
        f"  agentagon release gate — {result['pair_label']}",
        f"  {'-' * _DASH_WIDTH}",
    ]
    for row in result["diff"]:
        base_str = _measured_pct(row["baseline"])
        cand_str = _measured_pct(row["candidate"])
        lines.append(
            f"  {row['id']:<{_ID_WIDTH}} {base_str:>{_SIDE_WIDTH}} -> {cand_str:>{_SIDE_WIDTH}}  ~"
        )
    return "\n".join(lines)
