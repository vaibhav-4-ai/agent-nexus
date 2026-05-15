"""
LLM provider abstraction using LiteLLM.

Design Pattern: Strategy — swap backends (Groq/OpenAI/Anthropic/Ollama)
without changing calling code. Includes retry logic, circuit breaker,
streaming, and token tracking.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

import litellm
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.infra.logging import Timer, get_logger
from src.infra.metrics import get_metrics

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

        if json_mode:
            call_kwargs["response_format"] = {"type": "json_object"}

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
            logger.error("llm_stream_failed", model=model, error=str(e))
            raise

    @retry(
        retry=retry_if_exception_type((litellm.exceptions.RateLimitError, litellm.exceptions.ServiceUnavailableError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _call_with_retry(self, kwargs: dict[str, Any]) -> Any:
        """Make LLM call with automatic retry on rate limit / service errors."""
        try:
            return await litellm.acompletion(**kwargs)
        except (litellm.exceptions.RateLimitError, litellm.exceptions.ServiceUnavailableError):
            raise  # Let tenacity retry these
        except Exception as e:
            self._failure_count += 1
            if self._failure_count >= self._max_failures and not self._using_fallback:
                logger.error(
                    "llm_circuit_breaker_open",
                    primary_model=self._settings.model,
                    fallback_model=self._settings.fallback_model,
                )
                self._using_fallback = True
            logger.error("llm_call_failed", error=str(e), failure_count=self._failure_count)
            raise


# Module-level singleton
_llm_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get the singleton LLMProvider."""
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = LLMProvider()
    return _llm_provider
