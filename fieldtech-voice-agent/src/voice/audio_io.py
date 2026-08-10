"""Push-to-talk mic capture and speaker playback.

Seam: this is the only file telephony replaces. A SIP/Twilio media stream feeds the same
WAV bytes into `VoiceProvider.transcribe` and plays back what `synthesize` returns; the
orchestrator never notices.

sounddevice is imported lazily so `--text` mode runs on a box with no PortAudio.
"""

from __future__ import annotations

import io
import sys
import threading
import wave

from src.config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from src.telemetry import span


def _sounddevice():
    try:
        import sounddevice as sd

        return sd
    except Exception as exc:  # PortAudio missing, no audio device, etc.
        raise RuntimeError(
            f"Audio I/O unavailable ({type(exc).__name__}: {exc}). "
            "Install PortAudio and `sounddevice`, or use --text mode."
        ) from exc


def record_push_to_talk(prompt: str = "[press Enter to speak, Enter again to stop] ") -> bytes:
    """Record 16 kHz mono until the user presses Enter a second time. Returns WAV bytes."""
    sd = _sounddevice()
    input(prompt)
    frames: list[bytes] = []
    stop = threading.Event()

    def callback(indata, _frames, _time, status):
        if status:
            print(f"  (audio input status: {status})", file=sys.stderr)
        frames.append(bytes(indata))

    with span("voice.record") as attrs:
        with sd.RawInputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype="int16",
            callback=callback,
        ):
            print("  recording... (Enter to stop)")
            waiter = threading.Thread(target=lambda: (input(), stop.set()), daemon=True)
            waiter.start()
            while not stop.is_set():
                sd.sleep(50)

        pcm = b"".join(frames)
        attrs["audio_bytes"] = len(pcm)
        attrs["seconds"] = round(len(pcm) / (2 * AUDIO_SAMPLE_RATE * AUDIO_CHANNELS), 2)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(AUDIO_CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(AUDIO_SAMPLE_RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()


def play_wav(wav_bytes: bytes) -> None:
    """Blocking playback of WAV bytes."""
    sd = _sounddevice()
    import numpy as np

    with span("voice.playback") as attrs:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            channels = handle.getnchannels()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels)
        attrs["seconds"] = round(len(frames) / (2 * rate * channels), 2)
        sd.play(samples, rate)
        sd.wait()
