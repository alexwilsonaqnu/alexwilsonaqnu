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
    def transcribe(self, wav_bytes: bytes) -> str:
        """Speech to text."""

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
