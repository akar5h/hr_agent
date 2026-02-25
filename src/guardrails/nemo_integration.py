"""NeMo Guardrails integration for the HR recruitment agent.

Provides an opt-in toggle (ENABLE_NEMO_GUARDRAILS) that wraps the
primary ChatOpenAI model with NeMo RunnableRails. When disabled or
when the nemoguardrails package is not installed, the original model
is returned unchanged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

ENABLE_NEMO_GUARDRAILS = os.getenv("ENABLE_NEMO_GUARDRAILS", "false").lower() == "true"

logger = logging.getLogger(__name__)

_NEMO_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "nemo_config"


def load_nemo_config():
    """Load RailsConfig from the nemo_config/ directory at project root."""
    from nemoguardrails import RailsConfig

    return RailsConfig.from_path(str(_NEMO_CONFIG_DIR))


def wrap_model_with_guardrails(model, session_client_id: str = "default"):
    """Wrap a ChatOpenAI model with NeMo Guardrails if enabled.

    Returns the original model unchanged when:
    - ENABLE_NEMO_GUARDRAILS is false (default)
    - nemoguardrails package is not installed
    - Config loading fails for any reason
    """
    if not ENABLE_NEMO_GUARDRAILS:
        return model

    try:
        from nemoguardrails.integrations.langchain.runnable_rails import RunnableRails

        config = load_nemo_config()
        guardrails = RunnableRails(config, passthrough=True)
        logger.info("NeMo Guardrails enabled for client %s", session_client_id)
        return guardrails | model
    except ImportError:
        logger.warning("nemoguardrails not installed, skipping guardrail wrapping")
        return model
    except Exception:
        logger.error("Failed to load NeMo Guardrails", exc_info=True)
        return model
