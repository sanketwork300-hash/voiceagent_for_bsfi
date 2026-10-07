"""VAD, end-of-turn detection and barge-in configuration for LiveKit Agents."""

from __future__ import annotations

from typing import Any

from app.config import Settings


def load_vad():
    from livekit.plugins import silero

    return silero.VAD.load(min_silence_duration=0.35, activation_threshold=0.5)


def load_turn_detector():
    """Multilingual semantic end-of-utterance model (covers Hindi + English); falls back to VAD endpointing."""
    try:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        return MultilingualModel()
    except Exception:  # model files not downloaded -> run `python -m app.channels.voice.livekit_agent download-files`
        return "vad"


def turn_handling(settings: Settings) -> dict[str, Any]:
    return {
        "turn_detection": load_turn_detector(),
        "endpointing": {"min_delay": settings.voice_min_endpointing_delay, "max_delay": settings.voice_max_endpointing_delay},
        # barge-in: user speech >= min_duration interrupts TTS; false interruptions (coughs) resume playback
        "interruption": {"enabled": True, "min_duration": settings.voice_min_interruption_duration, "min_words": 1,
                         "resume_false_interruption": True},
        "preemptive_generation": {"enabled": False},  # never start tool calls on a partial transcript
    }
