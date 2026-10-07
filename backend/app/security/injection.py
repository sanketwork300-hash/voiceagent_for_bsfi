"""Prompt-injection heuristics for untrusted text (retrieved documents, tool outputs, user input).

Detection is a signal, not the defence: the real controls are that the LLM cannot grant itself tools,
change authentication state, or bypass the policy engine regardless of what any text says.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (the |your )?(system|previous|above) (prompt|instructions)",
    r"you are now (in )?(developer|dan|admin|god) mode",
    r"(reveal|print|show|repeat) (your |the )?(system prompt|hidden instructions|instructions above)",
    r"\bact as (an? )?(admin|administrator|bank officer|developer)\b",
    r"(call|invoke|execute|use) (the )?(tool|function) .{0,40}(without|skip|bypass)",
    r"(bypass|skip|disable|override) (the )?(policy|authentication|otp|verification|confirmation|security)",
    r"(the )?(customer|user) (is|has been) (already )?(verified|authenticated|authori[sz]ed)",
    r"transfer .{0,30} to (account|a/c) .{0,30} (immediately|now) (without|no) ",
    r"</?(system|assistant|tool)>",
    r"\bBEGIN (SYSTEM|ADMIN) (PROMPT|OVERRIDE)\b",
]
_RX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I)


def injection_score(text: str) -> float:
    if not text:
        return 0.0
    hits = len(_RX.findall(text))
    return min(1.0, hits * 0.5)


def looks_like_injection(text: str, threshold: float = 0.5) -> bool:
    return injection_score(text) >= threshold


def neutralize(text: str) -> str:
    """Defang instruction-like spans inside untrusted data so they read as quoted content."""
    return _RX.sub(lambda m: f"[removed suspicious instruction: {m.group(0)[:40]!r}]", text)
