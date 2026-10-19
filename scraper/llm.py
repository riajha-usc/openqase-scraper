"""One function, `parse`, that returns a validated Pydantic object from an LLM.

Two providers, chosen with OQ_PROVIDER:
  anthropic          (default) Anthropic SDK with native structured outputs. Needs ANTHROPIC_API_KEY.
  openai_compatible  any OpenAI-style chat endpoint: Gemini's compatibility API, Groq, Ollama, etc.
                     Needs OQ_BASE_URL, OQ_API_KEY and OQ_MODEL.
"""
import json
import os
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from .config import MODEL

T = TypeVar("T", bound=BaseModel)
PROVIDER = os.environ.get("OQ_PROVIDER", "anthropic")
_anthropic_client = None
_openai_client = None


class LLMRefusal(RuntimeError):
    pass


# ---------------------------------------------------------------- Anthropic
def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _anthropic_client


def _parse_anthropic(system: str, user: str, schema: Type[T], max_tokens: int) -> T:
    response = _anthropic().messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        # The system prompt is identical across calls, so mark it cacheable.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None) or "model refused"
        raise LLMRefusal(detail)
    if response.parsed_output is None:
        raise RuntimeError(f"No structured output (stop_reason={response.stop_reason})")
    return response.parsed_output


# ------------------------------------------------------- OpenAI-compatible
def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        base_url = os.environ.get("OQ_BASE_URL")
        if not base_url:
            raise SystemExit("OQ_PROVIDER=openai_compatible needs OQ_BASE_URL (and OQ_API_KEY) in .env.local")
        _openai_client = OpenAI(base_url=base_url, api_key=os.environ.get("OQ_API_KEY", "none"))
    return _openai_client


def _parse_openai_compatible(system: str, user: str, schema: Type[T], max_tokens: int) -> T:
    """JSON-mode prompting plus Pydantic validation, with one corrective retry.

    Schema-enforced output varies between providers, so the schema goes in the prompt and the
    reply is validated locally. That works the same on Gemini, Groq and Ollama.
    """
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    system_with_schema = (f"{system}\n\nRespond with a single JSON object and nothing else. "
                          f"It must validate against this JSON schema:\n{schema_json}")
    messages = [{"role": "system", "content": system_with_schema}, {"role": "user", "content": user}]
    last_error = None
    for _ in range(2):
        completion = _openai().chat.completions.create(
            model=MODEL, messages=messages, max_tokens=max_tokens, temperature=0,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content or ""
