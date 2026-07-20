"""DeepEval-compatible LLM wrapper around a single AWS Bedrock model.

Used as the ``simulator_model`` passed to ``deepeval.simulator.ConversationSimulator``.
Talks to Bedrock via ``boto3`` ``bedrock-runtime`` ``.converse()`` — the SAME boto3
client mechanism that ``runner.cost_ledger.install_ledger()`` patches, so every
call this class makes is automatically tallied in the process-wide token ledger.

The simulator models used in this experiment (moonshotai.kimi-k2.5, zai.glm-4.7)
are decorrelated from the Qwen agent model on purpose (see EXPERIMENT_PLAN.md §2).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Type, TypeVar

import boto3
from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.4


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Drop the opening fence line (``` or ```json) and the trailing ``` fence.
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


class BedrockSimLLM(DeepEvalBaseLLM):
    """A DeepEvalBaseLLM backed by a single Bedrock model via the Converse API."""

    def __init__(
        self,
        model_id: str,
        region_name: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self.model_id = model_id
        self.region_name = region_name or os.getenv("BEDROCK_REGION", "ap-south-1")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client: Any = None
        try:
            super().__init__(model_id)
        except TypeError:
            # Some DeepEvalBaseLLM versions take no args / different args.
            super().__init__()

    # -- DeepEvalBaseLLM required interface ---------------------------------

    def load_model(self, *args: Any, **kwargs: Any) -> Any:
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self.region_name)
        return self._client

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return self.model_id

    @staticmethod
    def _find_schema(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """DeepEval calls generate/a_generate with a pydantic schema class as the
        2nd positional arg (or schema=...) when it wants structured output. Detect
        it (duck-typed on model_validate) and route to the schema path."""
        schema = kwargs.get("schema")
        if schema is None:
            for a in args:
                if isinstance(a, type) and hasattr(a, "model_validate"):
                    schema = a
                    break
        return schema

    def _raw_text(self, prompt: str) -> str:
        client = self.load_model()
        response = client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
        )
        return response["output"]["message"]["content"][0]["text"]

    def generate(self, prompt: str, *args: Any, **kwargs: Any) -> Any:
        schema = self._find_schema(args, kwargs)
        if schema is not None:
            return self.generate_with_schema(prompt, schema)
        return self._raw_text(prompt)

    async def a_generate(self, prompt: str, *args: Any, **kwargs: Any) -> Any:
        schema = self._find_schema(args, kwargs)
        if schema is not None:
            return await self.a_generate_with_schema(prompt, schema)
        return await asyncio.to_thread(self._raw_text, prompt)

    def generate_with_schema(self, prompt: str, schema: Type[T], *args: Any, **kwargs: Any) -> T:
        """Structured generation: ask the model for raw JSON matching ``schema``,
        parse it, and validate into a pydantic instance. Retries once on any
        parse/validation failure with a stricter follow-up prompt.
        """
        schema_json = json.dumps(schema.model_json_schema())
        structured_prompt = (
            f"{prompt}\n\n"
            "Respond with ONLY a single JSON object — no prose, no markdown code "
            "fences, no explanation — that strictly matches this JSON schema:\n"
            f"{schema_json}"
        )

        last_error: Exception | None = None
        for attempt in range(2):
            raw = self.generate(structured_prompt)
            cleaned = _strip_code_fences(raw)
            try:
                parsed = json.loads(cleaned)
                return schema.model_validate(parsed)
            except Exception as exc:  # json.JSONDecodeError or pydantic ValidationError
                last_error = exc
                structured_prompt = (
                    f"{prompt}\n\n"
                    "Your previous response could not be parsed as valid JSON matching "
                    f"this schema:\n{schema_json}\n\n"
                    "Respond again with ONLY the raw JSON object — no markdown fences, "
                    "no commentary, no leading/trailing text."
                )
        raise ValueError(
            f"BedrockSimLLM.generate_with_schema: failed to produce valid "
            f"{schema.__name__} JSON after 2 attempts: {last_error}"
        )

    async def a_generate_with_schema(self, prompt: str, schema: Type[T], *args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(self.generate_with_schema, prompt, schema, *args, **kwargs)
