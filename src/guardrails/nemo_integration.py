"""NeMo Guardrails integration for the HR recruitment agent.

Instead of wrapping the model (which breaks LangGraph's bind_tools),
we run NeMo's LLMRails as a standalone input/output checker.

- check_input(text) → (allowed: bool, response: str|None)
- check_output(text) → (allowed: bool, response: str|None)

When ENABLE_NEMO_GUARDRAILS is false or nemoguardrails is not installed,
both functions return (True, None) — everything passes through.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from src.observability.decorators import traced

ENABLE_NEMO_GUARDRAILS = os.getenv("ENABLE_NEMO_GUARDRAILS", "false").lower() == "true"

logger = logging.getLogger(__name__)

_NEMO_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "nemo_config"

# Singleton LLMRails instance (initialized lazily)
_rails_instance = None
_rails_init_failed = False


def _get_rails():
    """Lazily initialize and return the LLMRails singleton."""
    global _rails_instance, _rails_init_failed

    if _rails_init_failed:
        return None
    if _rails_instance is not None:
        return _rails_instance

    try:
        from nemoguardrails import LLMRails, RailsConfig

        config = RailsConfig.from_path(str(_NEMO_CONFIG_DIR))
        _rails_instance = LLMRails(config)
        logger.info("NeMo LLMRails initialized from %s", _NEMO_CONFIG_DIR)
        return _rails_instance
    except ImportError:
        logger.warning("nemoguardrails not installed, guardrails disabled")
        _rails_init_failed = True
        return None
    except Exception:
        logger.error("Failed to initialize NeMo LLMRails", exc_info=True)
        _rails_init_failed = True
        return None


@traced(name="guardrails-check-input")
async def check_input(text: str) -> tuple[bool, Optional[str]]:
    """Check user input against NeMo input rails.

    Returns (True, None) if allowed.
    Returns (False, refusal_message) if blocked.
    """
    if not ENABLE_NEMO_GUARDRAILS:
        return True, None

    rails = _get_rails()
    if rails is None:
        return True, None

    try:
        response = await rails.generate_async(
            messages=[{"role": "user", "content": text}]
        )
        bot_message = response.get("content", "") if isinstance(response, dict) else str(response)

        # NeMo returns a refusal message when input is blocked
        refusal_markers = [
            "i cannot", "i can't", "i'm not able", "not allowed",
            "refuse to respond", "cannot help with that",
        ]
        if any(marker in bot_message.lower() for marker in refusal_markers):
            logger.warning("NeMo input rail BLOCKED: %s", text[:120])
            return False, bot_message

        return True, None
    except Exception:
        logger.error("NeMo input check failed", exc_info=True)
        return True, None


@traced(name="guardrails-check-output")
async def check_output(text: str) -> tuple[bool, Optional[str]]:
    """Check agent output against NeMo output rails.

    Returns (True, None) if allowed.
    Returns (False, safe_response) if blocked.
    """
    if not ENABLE_NEMO_GUARDRAILS:
        return True, None

    rails = _get_rails()
    if rails is None:
        return True, None

    try:
        # Run output through the rails by simulating a conversation
        response = await rails.generate_async(
            messages=[
                {"role": "user", "content": "What is the evaluation result?"},
                {"role": "assistant", "content": text},
            ]
        )
        bot_message = response.get("content", "") if isinstance(response, dict) else str(response)

        refusal_markers = [
            "cannot share", "sensitive data", "not allowed",
        ]
        if any(marker in bot_message.lower() for marker in refusal_markers):
            logger.warning("NeMo output rail BLOCKED response: %s", text[:120])
            return False, bot_message

        return True, None
    except Exception:
        logger.error("NeMo output check failed", exc_info=True)
        return True, None


# Keep the old function signature for backward compatibility,
# but now it's a no-op — guardrails are applied in server.py
def wrap_model_with_guardrails(model, session_client_id: str = "default"):
    """No-op: guardrails are now applied as input/output checks in server.py."""
    if ENABLE_NEMO_GUARDRAILS:
        # Ensure rails are initialized eagerly so config errors surface at startup
        _get_rails()
        logger.info("NeMo Guardrails active (input/output mode) for client %s", session_client_id)
    return model
