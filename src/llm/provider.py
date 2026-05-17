"""
LLM provider abstraction using LiteLLM.

Design Pattern: Strategy — swap backends (Groq/OpenAI/Anthropic/Ollama)
without changing calling code. Includes retry logic, circuit breaker,
streaming, and token tracking.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator

import litellm
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.infra.logging import Timer, get_logger, redact_secrets
from src.infra.metrics import get_metrics

# ---------------------------------------------------------------------------
# Per-request BYOK override
# ---------------------------------------------------------------------------
# A task may supply its own provider + model + api_key (a "BYOK" override).
# We thread it through via a ContextVar so individual call sites (planner,
# executor, verifier, etc.) don't all need their function signatures changed.
#
# Lifecycle: engine.execute_task() opens `byok_override(...)` around its body;
# inside that block, `get_byok()` returns the override dict; outside, it's None.
_byok_ctx: ContextVar[dict[str, Any] | None] = ContextVar("byok_override", default=None)


def get_byok() -> dict[str, Any] | None:
    """Return the current request's BYOK override, or None if none set."""
    return _byok_ctx.get()


@contextmanager
def byok_override(byok: dict[str, Any] | None) -> Iterator[None]:
    """Context manager that activates a BYOK override for the duration of a block.

    Args:
        byok: dict with keys 'provider', 'model', 'api_key'. If None, this is
              a no-op (server's default credentials are used).
    """
    if not byok:
        yield
        return
    token = _byok_ctx.set(byok)
    try:
        yield
    finally:
        _byok_ctx.reset(token)

logger = get_logger("llm.provider")

# Suppress litellm's verbose logging
litellm.set_verbose = False


class LLMResponse:
    """Standardized LLM response wrapper."""

    def __init__(
        self,
        content: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        finish_reason: str = "stop",
        raw_response: Any = None,
    ) -> None:
        self.content = content
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.finish_reason = finish_reason
        self.raw_response = raw_response

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "finish_reason": self.finish_reason,
        }


class LLMProvider:
    """
    Unified LLM provider using LiteLLM as the backend.

    Supports Groq, OpenAI, Anthropic, Ollama, vLLM, and any LiteLLM-compatible provider.
    Includes automatic retry, circuit breaker, token tracking, and streaming.
    """

    def __init__(self) -> None:
        self._settings = get_settings().llm
        self._failure_count = 0
        self._max_failures = 5
        self._using_fallback = False

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Make an LLM completion call with retry and fallback.

        Args:
            messages: Chat messages in OpenAI format.
            model: Override model name (litellm format: provider/model).
            temperature: Override temperature.
            max_tokens: Override max tokens.
            json_mode: If True, request JSON output.
        """
        # BYOK override (per-request): when active, use the user's model + key
        # directly. Server-side singleton state (_using_fallback, _failure_count)
        # is ignored so one user's BYOK call doesn't trip the server's circuit
        # breaker, and one user's rate limit doesn't affect anyone else.
        byok = get_byok()
        if byok is not None:
            model = byok["model"]
            # api_key passed per-call so it never lands in os.environ
            kwargs["api_key"] = byok["api_key"]
        else:
            model = model or (self._settings.fallback_model if self._using_fallback
                              else self._settings.model)
        temperature = temperature if temperature is not None else self._settings.temperature
        max_tokens = max_tokens or self._settings.max_tokens

        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self._settings.timeout,
            **kwargs,
        }

        # NOTE: We deliberately do NOT set response_format={"type":"json_object"}.
        # Groq's server-side strict JSON validator rejects valid-but-loose JSON
        # (e.g. plans with embedded Python code in `code` arguments), returning
        # litellm.BadRequestError BEFORE the model's text reaches us — bypassing
        # the planner's retry loop. The system prompts already instruct JSON
        # output; structured_output.extract_json() + parse_llm_response() handle
        # tolerant parsing, and the planner re-prompts on ValueError.
        _ = json_mode  # parameter retained for caller-side compatibility

        metrics = await get_metrics()
        await metrics.increment("agent_llm_calls_total", labels={"provider": model.split("/")[0]})

        with Timer(logger, "llm_call", model=model):
            response = await self._call_with_retry(call_kwargs)

        # Extract token usage
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

        # Track tokens
        provider_name = model.split("/")[0] if "/" in model else "unknown"
        await metrics.record_tokens(provider_name, input_tokens, output_tokens)

        content = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason or "stop"

        self._failure_count = 0  # Reset on success

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            raw_response=response,
        )

    async def complete_with_vision(
        self,
        text_prompt: str,
        image_data: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Make a vision-capable LLM call.

        Args:
            text_prompt: The text question/instruction.
            image_data: List of dicts with 'type' and 'url' or 'base64' keys.
        """
        model = model or self._settings.vision_model

        content_parts: list[dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        for img in image_data:
            if "url" in img:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img["url"]},
                })
            elif "base64" in img:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img['base64']}"},
                })

        messages = [{"role": "user", "content": content_parts}]
        return await self.complete(messages, model=model, **kwargs)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        Stream an LLM completion, yielding content chunks.

        Usage:
            async for chunk in provider.stream(messages):
                print(chunk, end="")
        """
        model = model or self._settings.model
        temperature = temperature if temperature is not None else self._settings.temperature
        max_tokens = max_tokens or self._settings.max_tokens

        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                timeout=self._settings.timeout,
                **kwargs,
            )

            async for chunk in response:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content

        except Exception as e:
            logger.error("llm_stream_failed",
                         model=model,
                         error_type=type(e).__name__,
                         error=redact_secrets(str(e))[:300])
            raise

    @retry(
        retry=retry_if_exception_type((litellm.exceptions.ServiceUnavailableError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _call_with_retry(self, kwargs: dict[str, Any]) -> Any:
        """Make LLM call with rate-limit failover + transient-error retry.

        - ServiceUnavailableError: tenacity short-retries (transient outage).
        - RateLimitError: swap to fallback_model once. Groq's free tier has
          separate per-model TPD budgets, so the fallback usually has headroom
          even when the primary is exhausted (8b has 5x the primary's daily
          token budget — both free). 8-second exponential retry is useless for
          daily-quota errors saying "wait 8 minutes."
        """
        try:
            return await litellm.acompletion(**kwargs)
        except litellm.exceptions.RateLimitError as e:
            current_model = kwargs.get("model", "")
            fallback = self._settings.fallback_model
            # L3-B: LiteLLM exception text can include the request URL with a
            # key embedded. Redact + truncate before logging.
            err_text = redact_secrets(str(e))[:300]
            # BYOK calls (identified by per-call api_key in kwargs) propagate
            # rate-limit errors directly. We don't trigger the server-side
            # fallback model because that would attempt the user's fallback
            # without their key, and would pollute the singleton's state.
            if "api_key" in kwargs:
                logger.warning("byok_rate_limit",
                               model=current_model,
                               error_type=type(e).__name__,
                               error=err_text)
                raise
            if not self._using_fallback and fallback and fallback != current_model:
                logger.warning(
                    "llm_rate_limit_failover",
                    primary=current_model,
                    fallback=fallback,
                    error_type=type(e).__name__,
                    error=err_text,
                )
                self._using_fallback = True
                fallback_kwargs = {**kwargs, "model": fallback}
                return await litellm.acompletion(**fallback_kwargs)
            logger.error("llm_rate_limit_fatal",
                         model=current_model,
                         error_type=type(e).__name__,
                         error=err_text)
            raise
        except litellm.exceptions.ServiceUnavailableError:
            raise  # let tenacity retry transient outages
        except Exception as e:
            # BYOK call: don't touch the singleton's circuit-breaker state.
            # The error belongs to the visitor's credentials, not ours.
            if "api_key" in kwargs:
                logger.warning("byok_call_failed",
                               model=kwargs.get("model"),
                               error_type=type(e).__name__,
                               error=redact_secrets(str(e))[:300])
                raise
            self._failure_count += 1
            if self._failure_count >= self._max_failures and not self._using_fallback:
                logger.error(
                    "llm_circuit_breaker_open",
                    primary_model=self._settings.model,
                    fallback_model=self._settings.fallback_model,
                )
                self._using_fallback = True
            # L3-B: redact + truncate any embedded URL/key in the exception text.
            logger.error("llm_call_failed",
                         error_type=type(e).__name__,
                         error=redact_secrets(str(e))[:300],
                         failure_count=self._failure_count)
            raise


# Module-level singleton
_llm_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get the singleton LLMProvider."""
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = LLMProvider()
    return _llm_provider
