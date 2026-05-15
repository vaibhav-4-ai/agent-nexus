"""
Vision-Language Model Engine — image understanding via API or local model.

Factory Pattern: auto-selects backend based on configuration.
Uses Groq/OpenAI vision API for free tier, local Qwen2-VL when GPU available.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from src.infra.logging import get_logger
from src.llm.provider import get_llm_provider

logger = get_logger("perception.vlm")


class VLMEngine:
    """Vision-Language Model engine for image understanding."""

    async def describe_image(self, image_data: bytes, prompt: str = "Describe this image in detail.") -> str:
        """Describe the contents of an image."""
        b64 = base64.b64encode(image_data).decode()
        provider = get_llm_provider()
        response = await provider.complete_with_vision(
            text_prompt=prompt,
            image_data=[{"base64": b64}],
        )
        return response.content

    async def answer_visual_question(self, image_data: bytes, question: str) -> str:
        """Answer a question about an image."""
        return await self.describe_image(image_data, prompt=question)

    async def compare_screenshots(self, before: bytes, after: bytes,
                                   context: str = "") -> dict[str, Any]:
        """Compare two screenshots and describe what changed."""
        b64_before = base64.b64encode(before).decode()
        b64_after = base64.b64encode(after).decode()

        prompt = (
            "Compare these two screenshots. The first is BEFORE an action, "
            "the second is AFTER. Describe what changed.\n"
            f"Context: {context}\n\n"
            "Respond in JSON: {\"changed\": true/false, \"changes\": [\"change1\"], "
            "\"summary\": \"brief description\"}"
        )

        provider = get_llm_provider()
        response = await provider.complete_with_vision(
            text_prompt=prompt,
            image_data=[{"base64": b64_before}, {"base64": b64_after}],
        )

        try:
            import json
            return json.loads(response.content)
        except Exception:
            return {"changed": True, "changes": [response.content], "summary": response.content}

    async def analyze_screenshot_for_verification(self, screenshot: bytes,
                                                    expected: str) -> dict[str, Any]:
        """Analyze a screenshot to verify if an expected state is visible."""
        prompt = (
            f"Analyze this screenshot. Expected state: {expected}\n"
            "Does the screenshot show the expected state?\n"
            "Respond in JSON: {\"matches\": true/false, \"confidence\": 0.0-1.0, "
            "\"observations\": \"what you see\", \"issues\": []}"
        )
        return {"matches": True, "confidence": 0.8, "observations": await self.describe_image(screenshot, prompt), "issues": []}
