"""Context window compression for long agent conversations.

Implements a sliding window summarization pattern: when the message list exceeds
MAX_MESSAGES_BEFORE_COMPRESS, older tool call/result pairs are compressed into a
single summary SystemMessage, keeping the last KEEP_RECENT_MESSAGES intact.

Pattern used by: Cognition Devin, LangMem, LangChain summarize_chat_history.

Usage (in workflow.py or app.py):
    from src.graph.compression import compress_messages
    messages = compress_messages(messages, model)
"""

from __future__ import annotations

import os
from typing import Any

MAX_MESSAGES_BEFORE_COMPRESS = 20
KEEP_RECENT_MESSAGES = 8
TOKEN_COMPRESS_THRESHOLD = int(os.getenv("TOKEN_COMPRESS_THRESHOLD", "32000"))
TOKEN_KEEP_BUDGET = 8000


def count_tokens_approximate(text: str) -> int:
    """Count tokens using tiktoken if installed, else char/4 heuristic."""
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        return len(encoder.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def count_messages_tokens(messages: list[Any]) -> int:
    """Estimate total tokens across message contents."""
    total = 0
    for message in messages:
        content = getattr(message, "content", "")
        if isinstance(content, list):
            blocks: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    blocks.append(str(block.get("text", "")))
                else:
                    blocks.append(str(block))
            content = " ".join(blocks)
        total += count_tokens_approximate(str(content))
    return total


def compress_messages(messages: list[Any], model: Any) -> list[Any]:
    """Summarize old messages into a single SystemMessage to reduce context length.

    - System messages are always kept intact at the front.
    - The last KEEP_RECENT_MESSAGES messages are kept intact.
    - Everything in between is summarized via a single LLM call.
    - If fewer than MAX_MESSAGES_BEFORE_COMPRESS messages exist, returns unchanged.
    """
    if len(messages) <= MAX_MESSAGES_BEFORE_COMPRESS:
        return messages

    system_messages = [m for m in messages if getattr(m, "type", "") == "system"]
    non_system = [m for m in messages if getattr(m, "type", "") != "system"]

    if len(non_system) <= KEEP_RECENT_MESSAGES:
        return messages

    to_compress = non_system[: len(non_system) - KEEP_RECENT_MESSAGES]
    keep = non_system[len(non_system) - KEEP_RECENT_MESSAGES :]

    history_text = "\n\n".join(
        f"[{getattr(m, 'type', 'msg').upper()}]: {str(getattr(m, 'content', ''))[:500]}"
        for m in to_compress
    )
    summary_prompt = (
        "Summarize the following agent conversation history in 3-5 sentences.\n"
        "Preserve: candidate names evaluated, scores given, rubric used, key findings.\n"
        "Omit: raw tool output text, repeated reasoning, intermediate steps.\n\n"
        f"History:\n{history_text}"
    )

    try:
        summary_response = model.invoke(summary_prompt)
        summary_text = getattr(summary_response, "content", str(summary_response))
    except Exception:
        # If compression fails, return messages unchanged — don't break the agent.
        return messages

    from langchain_core.messages import SystemMessage

    summary_msg = SystemMessage(content=f"[Compressed history]: {summary_text}")
    return system_messages + [summary_msg] + keep


def compress_messages_token_aware(messages: list[Any], model: Any) -> list[Any]:
    """Compress only when estimated token count crosses threshold."""
    estimated = count_messages_tokens(messages)
    if estimated < TOKEN_COMPRESS_THRESHOLD:
        return messages
    return compress_messages(messages, model)
