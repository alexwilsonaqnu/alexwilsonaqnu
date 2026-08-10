"""Fish Audio provider (default).

Verified against docs.fish.audio (2026-08):
  TTS  POST https://api.fish.audio/v1/tts   — JSON body, `model` header
       (s1 | s2-pro | s2.1-pro | s2.1-pro-free), returns a chunked audio stream.
  ASR  POST https://api.fish.audio/v1/asr   — multipart/form-data only. The endpoint is
       in beta and explicitly does NOT accept JSON or base64 audio, which is the drift
       that bites people who code this from memory.
"""

from __future__ import annotations

import os

from src.config import (
    AUDIO_SAMPLE_RATE,
    DEFAULT_FISH_TTS_MODEL,
    FISH_ASR_URL,
    FISH_TTS_URL,
    HTTP_TIMEOUT_SECONDS,
)
from src.telemetry import span
from src.voice.base import VoiceProvider, looks_like_wav, pcm_to_wav


class FishAudioProvider(VoiceProvider):
    name = "fish"

    def __init__(self) -> None:
        # .strip(): see the note in the ElevenLabs provider — whitespace looks like a 401.
        self.api_key = (os.environ.get("FISH_AUDIO_API_KEY") or "").strip()
        if not self.api_key:
            raise RuntimeError(
                "FISH_AUDIO_API_KEY is not set. Export it, or run with --provider elevenlabs."
            )
        self.voice_id = os.environ.get("FISH_AUDIO_VOICE_ID") or None
        self.tts_model = os.environ.get("FISH_AUDIO_TTS_MODEL", DEFAULT_FISH_TTS_MODEL)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def transcribe(self, wav_bytes: bytes) -> str:
        import httpx

        with span("voice.stt", provider=self.name) as attrs:
            attrs["audio_bytes"] = len(wav_bytes)
            response = httpx.post(
                FISH_ASR_URL,
                headers=self._headers(),
                files={"audio": ("audio.wav", wav_bytes, "audio/wav")},
                data={"language": "en", "ignore_timestamps": "true"},
                timeout=HTTP_TIMEOUT_SECONDS * 4,
            )
            response.raise_for_status()
            text = (response.json().get("text") or "").strip()
            attrs["chars"] = len(text)
            return text

    def synthesize(self, text: str) -> bytes:
        import httpx

        with span("voice.tts", provider=self.name, model=self.tts_model) as attrs:
            attrs["chars"] = len(text)
            payload: dict[str, object] = {
                "text": text,
                "format": "wav",
                "sample_rate": AUDIO_SAMPLE_RATE,
                "latency": "balanced",
            }
            if self.voice_id:
                payload["reference_id"] = self.voice_id
            response = httpx.post(
                FISH_TTS_URL,
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                    "model": self.tts_model,
                },
                json=payload,
                timeout=HTTP_TIMEOUT_SECONDS * 4,
            )
            response.raise_for_status()
            audio = response.content
            attrs["audio_bytes"] = len(audio)
            return audio if looks_like_wav(audio) else pcm_to_wav(audio)
