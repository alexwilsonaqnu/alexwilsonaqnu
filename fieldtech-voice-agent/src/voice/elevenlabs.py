"""ElevenLabs provider.

Verified against elevenlabs.io/docs (2026-08):
  STT  POST https://api.elevenlabs.io/v1/speech-to-text          — multipart, `file` +
       `model_id` (scribe_v1 | scribe_v2), auth via the `xi-api-key` header.
  TTS  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=...
       — JSON body with `text` and `model_id`; returns raw audio in the requested codec.
       We ask for pcm_16000 and wrap it, so the provider seam always hands back WAV.
"""

from __future__ import annotations

import os

from src.config import (
    AUDIO_SAMPLE_RATE,
    DEFAULT_ELEVENLABS_STT_MODEL,
    DEFAULT_ELEVENLABS_TTS_MODEL,
    DEFAULT_ELEVENLABS_VOICE_ID,
    ELEVENLABS_STT_URL,
    ELEVENLABS_TTS_URL,
    HTTP_TIMEOUT_SECONDS,
)
from src.telemetry import span
from src.voice.base import VoiceProvider, looks_like_wav, pcm_to_wav


class ElevenLabsProvider(VoiceProvider):
    name = "elevenlabs"

    def __init__(self) -> None:
        # .strip(): a trailing newline survives a copy-paste and yields a 401 that reads
        # exactly like an invalid key.
        self.api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        if not self.api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set. Export it, or run with --provider fish."
            )
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID)
        self.stt_model = os.environ.get("ELEVENLABS_STT_MODEL", DEFAULT_ELEVENLABS_STT_MODEL)
        self.tts_model = os.environ.get("ELEVENLABS_TTS_MODEL", DEFAULT_ELEVENLABS_TTS_MODEL)

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key}

    def transcribe(self, wav_bytes: bytes) -> str:
        import httpx

        with span("voice.stt", provider=self.name, model=self.stt_model) as attrs:
            attrs["audio_bytes"] = len(wav_bytes)
            response = httpx.post(
                ELEVENLABS_STT_URL,
                headers=self._headers(),
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model_id": self.stt_model},
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
            response = httpx.post(
                ELEVENLABS_TTS_URL.format(voice_id=self.voice_id),
                headers={**self._headers(), "Content-Type": "application/json"},
                params={"output_format": f"pcm_{AUDIO_SAMPLE_RATE}"},
                json={"text": text, "model_id": self.tts_model},
                timeout=HTTP_TIMEOUT_SECONDS * 4,
            )
            response.raise_for_status()
            audio = response.content
            attrs["audio_bytes"] = len(audio)
            return audio if looks_like_wav(audio) else pcm_to_wav(audio)
