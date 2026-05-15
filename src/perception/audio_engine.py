"""
Audio Engine — speech-to-text and audio classification.

Uses HF Inference API for Whisper (free), falls back to local faster-whisper.
"""

from __future__ import annotations

import base64
import tempfile
from typing import Any

from src.infra.logging import get_logger

logger = get_logger("perception.audio")


class AudioEngine:
    """Audio processing engine for transcription and classification."""

    async def transcribe(self, audio_data: bytes, language: str = "en") -> str:
        """Transcribe audio to text."""
        # Try HF Inference API first (free)
        try:
            return await self._transcribe_hf(audio_data)
        except Exception as e:
            logger.warning("hf_transcription_failed", error=str(e))

        # Fallback to local faster-whisper
        try:
            return await self._transcribe_local(audio_data, language)
        except Exception as e:
            logger.error("all_transcription_failed", error=str(e))
            return f"[Transcription failed: {e}]"

    async def _transcribe_hf(self, audio_data: bytes) -> str:
        """Transcribe via Hugging Face Inference API."""
        import httpx
        import os

        hf_token = os.environ.get("HF_TOKEN", "")
        if not hf_token:
            raise ValueError("HF_TOKEN not set")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api-inference.huggingface.co/models/openai/whisper-large-v3",
                headers={"Authorization": f"Bearer {hf_token}"},
                content=audio_data,
                timeout=60.0,
            )
            response.raise_for_status()
            result = response.json()
            return result.get("text", "")

    async def _transcribe_local(self, audio_data: bytes, language: str) -> str:
        """Transcribe using local faster-whisper model."""
        from faster_whisper import WhisperModel

        model = WhisperModel("tiny", device="cpu", compute_type="int8")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(audio_data)
            tmp.flush()
            segments, _info = model.transcribe(tmp.name, language=language)
            return " ".join(segment.text for segment in segments)

    async def classify_audio(self, audio_data: bytes) -> dict[str, Any]:
        """Classify audio content (speech, music, noise, etc.)."""
        # Use a simple heuristic — in production, use CLAP or BEATs
        transcript = await self.transcribe(audio_data)
        if transcript and len(transcript) > 10:
            return {"type": "speech", "transcript": transcript, "confidence": 0.9}
        return {"type": "unknown", "transcript": transcript, "confidence": 0.3}
