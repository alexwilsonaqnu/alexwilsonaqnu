"""The VoiceProvider seam.

Everything above this interface is swappable (Fish, ElevenLabs, a telephony vendor's
built-in ASR); everything below it — the orchestrator, tools, hooks, skills — is not
allowed to care which one is in use.
"""

from __future__ import annotations

import io
import wave
from abc import ABC, abstractmethod

from src.config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE


class VoiceProvider(ABC):
    """16 kHz mono WAV in, 16 kHz mono WAV out."""

    name: str = "base"

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Speech to text. The mic loop sends WAV; the browser sends webm/opus."""

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Text to speech. Returns WAV bytes."""


def pcm_to_wav(
    pcm: bytes,
    sample_rate: int = AUDIO_SAMPLE_RATE,
    channels: int = AUDIO_CHANNELS,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw signed 16-bit PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def looks_like_wav(payload: bytes) -> bool:
    return len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"


_EXTENSIONS = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
}


def filename_for(mime_type: str) -> str:
    """Both speech APIs take the upload as multipart and infer the codec from the
    filename extension as well as the content type — send one that matches."""
    base = (mime_type or "").split(";")[0].strip().lower()
    return f"audio.{_EXTENSIONS.get(base, 'wav')}"
