"""LLM provider helpers for AWS Bedrock-backed chat models.

Plumbing: the agent runs on Bedrock (Converse API via langchain-aws). One
chokepoint — ``build_chat_model`` — with a per-call ``model`` override so each
level pins its own Bedrock model id. Credentials come from the boto3 default
chain (``AWS_PROFILE`` locally, the instance role on EC2).
"""

from __future__ import annotations

import os
from typing import Any

from langchain_aws import ChatBedrockConverse

from src.observability.decorators import traced

DEFAULT_BEDROCK_REGION = "ap-south-1"
DEFAULT_AGENT_MODEL = "deepseek.v3.2"
DEFAULT_SQLGEN_MODEL = "qwen.qwen3-32b-v1:0"
DEFAULT_FALLBACK_MODEL = "qwen.qwen3-235b-a22b-2507-v1:0"

# Backward-compat alias: older imports reference DEFAULT_OPENROUTER_MODEL.
DEFAULT_OPENROUTER_MODEL = DEFAULT_AGENT_MODEL


def prompt_cache_enabled() -> bool:
    """Whether to attach Anthropic-style cache_control blocks to the system prompt.

    Off by default on Bedrock — the open-weight models (deepseek/qwen/kimi/glm)
    do not use Anthropic cache_control blocks.
    """
    return os.getenv("ENABLE_PROMPT_CACHE", "false").lower() == "true"


def _model_supports_cache_control(model_name: str) -> bool:
    # Only Bedrock's Anthropic models honour cache_control content blocks.
    return model_name.startswith("anthropic.")


def _effective_temperature(temperature: float) -> float:
    """Realistic-low-temp override (doubt-review D6): a logged nonzero agent temp
    gives agent-side behavioural signal. Set BEDROCK_FORCE_TEMPERATURE to apply it
    across all call sites without editing each one; unset = use the passed value.
    """
    override = os.getenv("BEDROCK_FORCE_TEMPERATURE", "").strip()
    return float(override) if override else temperature


@traced(name="build-chat-model")
def build_chat_model(temperature: float = 0.0, model: str | None = None) -> Any:
    """Return a Bedrock Converse chat model (with a fallback) for the given level.

    ``model`` defaults to the agent model (env ``BEDROCK_AGENT_MODEL``); pass an
    explicit id for other levels (e.g. the SQL generator). A distinct fallback
    model absorbs throttling; identical primary/fallback skips the wrapper.
    """
    region = os.getenv("BEDROCK_REGION", DEFAULT_BEDROCK_REGION)
    model_id = model or os.getenv("BEDROCK_AGENT_MODEL", DEFAULT_AGENT_MODEL)
    fallback_id = os.getenv("BEDROCK_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
    temp = _effective_temperature(temperature)

    primary = ChatBedrockConverse(model=model_id, temperature=temp, region_name=region)

    if fallback_id and fallback_id != model_id:
        fallback = ChatBedrockConverse(
            model=fallback_id, temperature=temp, region_name=region
        )
        return primary.with_fallbacks([fallback])
    return primary
