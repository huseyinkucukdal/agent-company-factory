"""Sanitization helpers for response bodies.

Two responsibilities:

* :func:`safe_text` — defang ``<system>``-style tags and control characters.
* :func:`flag_injection_keys` — recursively scan a JSON-shaped value for
  keys that smell like prompt-injection attempts (``system``, ``instruction``,
  ``override``…). Returns the list of flagged dotted-paths.
"""
from __future__ import annotations

import re
from typing import Any

_TAG_RE = re.compile(r"<\s*/?\s*(system|assistant|user|tool|ignore)\b[^>]*>", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FLAGGED_KEYS = {
    "system",
    "instruction",
    "instructions",
    "override",
    "ignore_previous",
    "role",
}


def safe_text(text: str, *, max_length: int = 4_000) -> str:
    """Truncate, strip control chars, and defang role tags."""
    cleaned = _CONTROL_RE.sub(" ", text)
    cleaned = _TAG_RE.sub(lambda m: f"&lt;{m.group(0)[1:-1]}&gt;", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "...[truncated]"
    return cleaned


def flag_injection_keys(value: Any, _path: str = "") -> list[str]:
    flagged: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            sub_path = f"{_path}.{key}" if _path else str(key)
            if isinstance(key, str) and key.lower() in _FLAGGED_KEYS:
                flagged.append(sub_path)
            flagged.extend(flag_injection_keys(sub, sub_path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            flagged.extend(flag_injection_keys(item, f"{_path}[{i}]"))
    return flagged
