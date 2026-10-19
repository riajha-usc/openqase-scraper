"""One function, `parse`, that returns a validated Pydantic object from an LLM.

Two providers, chosen with OQ_PROVIDER:
  anthropic          (default) Anthropic SDK with native structured outputs. Needs ANTHROPIC_API_KEY.
  openai_compatible  any OpenAI-style chat endpoint: Gemini's compatibility API, Groq, Ollama, etc.
                     Needs OQ_BASE_URL, OQ_API_KEY and OQ_MODEL.
"""
import json
import os
import re
import time
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
        _openai_client = OpenAI(base_url=base_url, api_key=os.environ.get("OQ_API_KEY", "none"), max_retries=2)
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
        completion = _with_rate_limit_backoff(lambda: _openai().chat.completions.create(
            model=MODEL, messages=messages, max_tokens=max_tokens, temperature=0,
            response_format={"type": "json_object"},
        ))
        content = completion.choices[0].message.content or ""
        try:
            return schema.model_validate_json(_strip_fences(content))
        except ValidationError as e:
            last_error = e
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"That JSON did not validate: {e}. Return a corrected JSON object only."}]
    raise RuntimeError(f"Model output did not match {schema.__name__}: {last_error}")


class RequestTooLarge(RuntimeError):
    pass


def _parse_wait_seconds(message: str) -> float | None:
    """Groq writes 'try again in 442.5ms', '16.2s', '1m3.5s' or '2h10m'."""
    match = re.search(r"try again in ((?:\d+h)?(?:\d+m)?(?:[\d.]+m?s)?)", message)
    if not match or not match.group(1):
        return None
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(h|ms|m|s)", match.group(1)):
        total += float(value) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total or None


def _with_rate_limit_backoff(call, attempts: int = 6):
    """Free tiers meter tokens per minute; wait out a 429 instead of failing the page.

    A request whose own size exceeds the per-minute limit can never succeed, so that case
    raises at once with advice instead of retrying.
    """
    from openai import RateLimitError
    for attempt in range(attempts):
        try:
            return call()
        except RateLimitError as e:
            message = str(e)
            sizes = re.search(r"Limit (\d+), Used (\d+), Requested (\d+)", message)
            if sizes and int(sizes.group(3)) > int(sizes.group(1)):
                raise RequestTooLarge(
                    f"This request needs {sizes.group(3)} tokens but the provider allows {sizes.group(1)} per minute. "
                    "Lower OQ_MAX_PAGE_CHARS in .env.local (e.g. 12000) or use a provider with a higher limit.") from e
            if attempt == attempts - 1:
                raise
            wait = _parse_wait_seconds(message) or 15.0
            if wait > 300:
                raise RuntimeError(f"Provider rate limit: retry possible in {wait/60:.0f} minutes. {message[:200]}") from e
            wait = min(max(wait, 2.0), 120.0) + 1.0
            print(f"   rate limited; waiting {wait:.0f}s")
            time.sleep(wait)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


# ------------------------------------------------------------------ public
def parse(system: str, user: str, schema: Type[T], max_tokens: int = 8000) -> T:
    if PROVIDER == "anthropic":
        return _parse_anthropic(system, user, schema, max_tokens)
    if PROVIDER == "openai_compatible":
        return _parse_openai_compatible(system, user, schema, max_tokens)
    raise SystemExit(f"Unknown OQ_PROVIDER {PROVIDER!r}; use anthropic or openai_compatible")
