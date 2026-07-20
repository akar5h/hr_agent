"""Bedrock per-model token pricing for cost accounting.

# ESTIMATE — verify against real Bedrock ap-south-1 pricing before trusting the
# $45 budget projection. These numbers are placeholders pulled from public
# Bedrock on-demand pricing pages at write time and are NOT contractually
# accurate for ap-south-1. Override any of them via env vars
# (see ``_ENV_OVERRIDES`` below) once real pricing is confirmed in CP0/CP1.

All prices are USD per 1,000,000 tokens (per-mtok), split input/output.
"""

from __future__ import annotations

import os

# model_id -> {"in_per_mtok": float, "out_per_mtok": float}
# ESTIMATE — verify against real Bedrock ap-south-1 pricing.
PRICING: dict[str, dict[str, float]] = {
    "qwen.qwen3-235b-a22b-2507-v1:0": {"in_per_mtok": 0.20, "out_per_mtok": 0.60},
    "qwen.qwen3-32b-v1:0": {"in_per_mtok": 0.10, "out_per_mtok": 0.30},
    "moonshotai.kimi-k2.5": {"in_per_mtok": 0.55, "out_per_mtok": 2.20},
    "zai.glm-4.7": {"in_per_mtok": 0.40, "out_per_mtok": 1.50},
}

# Env var names used to override each model's per-mtok prices, e.g.:
#   PRICE_QWEN_235B_IN, PRICE_QWEN_235B_OUT
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "qwen.qwen3-235b-a22b-2507-v1:0": ("PRICE_QWEN_235B_IN", "PRICE_QWEN_235B_OUT"),
    "qwen.qwen3-32b-v1:0": ("PRICE_QWEN_32B_IN", "PRICE_QWEN_32B_OUT"),
    "moonshotai.kimi-k2.5": ("PRICE_KIMI_IN", "PRICE_KIMI_OUT"),
    "zai.glm-4.7": ("PRICE_GLM_IN", "PRICE_GLM_OUT"),
}


def _resolve_pricing(model: str) -> dict[str, float]:
    base = PRICING.get(model)
    if base is None:
        # Unknown model: assume the most expensive known rate so cost
        # projections never silently under-report. Callers should notice
        # this in the cost_report.json "unknown_models" section.
        base = {"in_per_mtok": 2.20, "out_per_mtok": 2.20}

    in_env, out_env = _ENV_OVERRIDES.get(model, ("", ""))
    in_price = float(os.getenv(in_env, base["in_per_mtok"])) if in_env else base["in_per_mtok"]
    out_price = float(os.getenv(out_env, base["out_per_mtok"])) if out_env else base["out_per_mtok"]
    return {"in_per_mtok": in_price, "out_per_mtok": out_price}


def usd(model: str, in_tok: int, out_tok: int) -> float:
    """Compute USD cost for a given model and token counts.

    Unknown model ids fall back to a conservative (expensive) estimate rather
    than raising, so a single unrecognised model id never crashes a scenario.
    """
    price = _resolve_pricing(model)
    return (in_tok / 1_000_000.0) * price["in_per_mtok"] + (out_tok / 1_000_000.0) * price["out_per_mtok"]


def is_known_model(model: str) -> bool:
    return model in PRICING
