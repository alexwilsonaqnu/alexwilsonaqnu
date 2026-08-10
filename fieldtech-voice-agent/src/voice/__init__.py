"""Voice layer. Thin surface over the agent core — nothing below here knows about audio."""

from __future__ import annotations

from src.voice.base import VoiceProvider


def get_provider(name: str) -> VoiceProvider:
    """Provider selection is the stable seam; telephony later replaces only audio_io."""
    key = (name or "").strip().lower()
    if key == "fish":
        from src.voice.fish import FishAudioProvider

        return FishAudioProvider()
    if key == "elevenlabs":
        from src.voice.elevenlabs import ElevenLabsProvider

        return ElevenLabsProvider()
    raise ValueError(f"Unknown voice provider '{name}'. Use 'fish' or 'elevenlabs'.")


__all__ = ["VoiceProvider", "get_provider"]
