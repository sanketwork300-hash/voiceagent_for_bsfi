"""STT / TTS provider selection per language, plus speech rendering of agent text.

STT, TTS, LLM and language detection are configured independently: e.g. Deepgram (multilingual, Hinglish
code-switching) or Sarvam (Indic-first) for STT, Sarvam/ElevenLabs for Indic TTS voices, any LLM.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.i18n.formatting import to_speech_text

SARVAM_LANG = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN", "ta": "ta-IN", "te": "te-IN", "bn": "bn-IN", "kn": "kn-IN",
               "gu": "gu-IN", "pa": "pa-IN", "ml": "ml-IN"}
DEFAULT_VOICES = {"sarvam": None, "elevenlabs": "EXAVITQu4vr4xnSDxMaL", "openai": "alloy"}  # None -> provider default


def create_stt(settings: Settings, language: str = "multi", voice_config: dict[str, Any] | None = None):
    provider = (voice_config or {}).get("stt_provider", settings.stt_provider)
    if provider == "deepgram":
        from livekit.plugins import deepgram

        # nova-3 "multi" handles English/Hindi code-switching without the caller choosing a language
        return deepgram.STT(model="nova-3", language="multi" if language in ("multi", "hi", "en") else language,
                            interim_results=True, smart_format=True, api_key=settings.deepgram_api_key)
    if provider == "sarvam":
        from livekit.plugins import sarvam

        return sarvam.STT(language=SARVAM_LANG.get(language, "unknown"), api_key=settings.sarvam_api_key)
    from livekit.plugins import openai

    return openai.STT(model="gpt-4o-transcribe", api_key=settings.openai_api_key)


def create_tts(settings: Settings, language: str = "en", voice_config: dict[str, Any] | None = None):
    vc = voice_config or {}
    provider = vc.get("tts_provider", settings.tts_provider)
    voice = (vc.get("voices") or {}).get(language) or vc.get("voice") or DEFAULT_VOICES.get(provider)
    if provider == "sarvam":
        from livekit.plugins import sarvam

        return sarvam.TTS(target_language_code=SARVAM_LANG.get(language, "en-IN"), speaker=voice, api_key=settings.sarvam_api_key)
    if provider == "elevenlabs":
        from livekit.plugins import elevenlabs

        return elevenlabs.TTS(voice_id=voice, model="eleven_flash_v2_5", api_key=settings.elevenlabs_api_key)
    from livekit.plugins import openai

    return openai.TTS(voice=voice, api_key=settings.openai_api_key)


def speech_text(text: str, language: str) -> str:
    """Agent text -> what the TTS should say (₹ amounts in lakh/crore words, masked ids, no citations)."""
    return to_speech_text(text, language)
