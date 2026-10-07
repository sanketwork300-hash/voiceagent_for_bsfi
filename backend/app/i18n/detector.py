"""Language detection for Indic + English text with code-switching (Hinglish, Tanglish, ...).

Script detection covers native-script input; a romanised-Hindi lexicon catches Hinglish, which is
the most common code-mixed form in Indian BFSI support. Channels may pass an STT language hint; the
detector only uses it when the text itself is inconclusive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SUPPORTED_LANGUAGES = {
    "en": "English", "hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
    "kn": "Kannada", "gu": "Gujarati", "pa": "Punjabi", "ml": "Malayalam",
}

_SCRIPTS: list[tuple[str, str, str]] = [  # (script, regex range, default language)
    ("Deva", "ऀ-ॿ", "hi"),
    ("Beng", "ঀ-৿", "bn"),
    ("Guru", "਀-੿", "pa"),
    ("Gujr", "઀-૿", "gu"),
    ("Taml", "஀-௿", "ta"),
    ("Telu", "ఀ-౿", "te"),
    ("Knda", "ಀ-೿", "kn"),
    ("Mlym", "ഀ-ൿ", "ml"),
]
_SCRIPT_RES = [(s, re.compile(f"[{r}]"), lang) for s, r, lang in _SCRIPTS]
_LATIN = re.compile(r"[A-Za-z]")

# Marathi-specific function words in Devanagari distinguish it from Hindi.
_MARATHI_MARKERS = re.compile(r"(आहे|आहेत|माझ्या|माझे|माझा|माझी|काय|कसे|नाही|करायचे|पाहिजे|किती)")

_ROMAN_HINDI = {
    "mera", "meri", "mere", "mujhe", "mujhko", "hai", "hain", "kya", "kitna", "kitni", "kitne", "kaise",
    "kab", "kahan", "kyun", "nahi", "nahin", "haan", "ha", "ji", "aap", "aapka", "aapke", "apna", "apni",
    "karo", "karna", "kar", "do", "dijiye", "chahiye", "chahta", "chahti", "batao", "bataiye", "ka", "ki",
    "ke", "ko", "se", "par", "mein", "bhi", "abhi", "paisa", "paise", "rupaye", "khata", "band", "bhejo",
    "bhej", "wala", "wali", "hua", "hui", "gaya", "gaye", "raha", "rahi", "tha", "thi", "baat", "samjha",
    "theek", "thik", "accha", "acha", "kripya", "dhanyavaad", "shukriya", "kal", "aaj", "agla", "pichla",
}
_ROMAN_TAMIL = {"enna", "evlo", "evvalavu", "enakku", "irukku", "venum", "illa", "sollunga", "panna", "en"}
_ROMAN_TELUGU = {"enti", "naaku", "ledu", "undi", "cheppandi", "kavali", "entha", "nenu"}
_ROMAN_MARATHI = {"aahe", "kiti", "majha", "majhi", "majhe", "kay", "nahi", "pahije", "kara"}
_ROMAN_LEXICONS = [("hi", _ROMAN_HINDI), ("ta", _ROMAN_TAMIL), ("te", _ROMAN_TELUGU), ("mr", _ROMAN_MARATHI)]
_WORD = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class DetectedLanguage:
    code: str  # ISO 639-1 (en, hi, ta, ...)
    script: str = "Latn"  # ISO 15924
    code_mixed: bool = False
    confidence: float = 1.0

    @property
    def label(self) -> str:
        if self.code == "hi" and self.script == "Latn":
            return "hinglish"
        return self.code

    @property
    def display_name(self) -> str:
        base = SUPPORTED_LANGUAGES.get(self.code, self.code)
        if self.script == "Latn" and self.code != "en":
            return f"{base} (Roman script, code-mixed with English)" if self.code_mixed else f"{base} (Roman script)"
        return base


class LanguageDetector:
    def detect(self, text: str, hint: str | None = None) -> DetectedLanguage:
        text = text or ""
        counts = {s: len(r.findall(text)) for s, r, _ in _SCRIPT_RES}
        latin = len(_LATIN.findall(text))
        script, n = max(counts.items(), key=lambda kv: kv[1])
        if n and n >= latin * 0.3:
            lang = next(lang for s, _, lang in _SCRIPT_RES if s == script)
            if script == "Deva" and _MARATHI_MARKERS.search(text):
                lang = "mr"
            return DetectedLanguage(lang, script, code_mixed=latin > 0, confidence=min(1.0, n / max(1, n + latin)))
        words = _WORD.findall(text.lower())
        if words:
            best, hits = "en", 0
            for lang, lex in _ROMAN_LEXICONS:
                h = sum(1 for w in words if w in lex)
                if h > hits:
                    best, hits = lang, h
            ratio = hits / len(words)
            if hits >= 2 or (hits == 1 and len(words) <= 3 and ratio >= 0.34):
                return DetectedLanguage(best, "Latn", code_mixed=True, confidence=min(1.0, 0.5 + ratio))
        if hint and hint.split("-")[0] in SUPPORTED_LANGUAGES and hint.split("-")[0] != "en" and not words:
            return DetectedLanguage(hint.split("-")[0], "Latn", confidence=0.4)
        return DetectedLanguage("en", "Latn", confidence=0.8 if words else 0.3)
