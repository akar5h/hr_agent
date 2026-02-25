"""Input sanitization gates used for optional hardening mode."""

from __future__ import annotations

import os
import re
import unicodedata

ENABLE_HARDENING = os.getenv("ENABLE_HARDENING", "false").lower() == "true"
MAX_INPUT_CHARS = int(os.getenv("HARDENING_MAX_INPUT_CHARS", "8000"))
INSTRUCTION_BOUNDARY = "### END OF SYSTEM INSTRUCTIONS ###"

_NULL_BYTES = re.compile(r"\x00")
_C0_CONTROLS = re.compile(r"[\x01-\x08\x0b-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]")
_BIDI_FORMATTING = re.compile(r"[\u202a-\u202e\u2066-\u2069\u200e\u200f]")


def sanitize(text: str) -> str:
    """Return sanitized text when hardening is enabled."""
    if not ENABLE_HARDENING:
        return text

    sanitized = unicodedata.normalize("NFC", text)
    sanitized = _NULL_BYTES.sub("", sanitized)
    sanitized = _C0_CONTROLS.sub("", sanitized)
    sanitized = _ZERO_WIDTH.sub("", sanitized)
    sanitized = _BIDI_FORMATTING.sub("", sanitized)

    if len(sanitized) > MAX_INPUT_CHARS:
        sanitized = sanitized[:MAX_INPUT_CHARS] + "\n[TRUNCATED]"

    if INSTRUCTION_BOUNDARY in sanitized:
        boundary_index = sanitized.index(INSTRUCTION_BOUNDARY)
        sanitized = sanitized[:boundary_index] + "\n[BOUNDARY ENFORCED]"

    return sanitized


def add_instruction_boundary(system_prompt: str) -> str:
    """Append the system prompt instruction boundary when hardening is enabled."""
    if not ENABLE_HARDENING:
        return system_prompt
    return system_prompt.rstrip() + f"\n\n{INSTRUCTION_BOUNDARY}\n"
