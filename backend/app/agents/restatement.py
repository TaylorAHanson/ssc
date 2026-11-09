"""Drop a reply's second, reworded copy of itself.

At temperature 0.0 the model sometimes finishes an answer and then writes it
again in other words, in the same message. The chat UI already collapses exact
repeats; this catches the reworded kind, where the second copy opens with a
near-identical sentence. It is conservative: it only cuts when a later sentence
closely matches the opening one and the text from there on is about as long as
the text before it, and it leaves anything with a code block alone.
"""
from __future__ import annotations

import logging
import re
from typing import List, Set

logger = logging.getLogger(__name__)

_MIN_CHARS = 120
_MIN_OPENING_CHARS = 40
_MIN_SIMILARITY = 0.75
_LENGTH_RATIO = (0.6, 1.6)

# A sentence starts after ., ! or ? plus whitespace, before a capital, a digit or
# inline markup. "e.g. main" doesn't split, because "main" is lowercase.
_SENTENCE_START = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`*_\[])")
_WORD = re.compile(r"\w+")


def _words(text: str) -> Set[str]:
    return set(_WORD.findall(text.lower()))


def _similarity(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _sentence_starts(text: str) -> List[int]:
    return [0] + [m.end() for m in _SENTENCE_START.finditer(text)]


def collapse_restated_reply(text: str) -> str:
    """Return ``text`` without a trailing reworded copy of itself, else unchanged."""
    body = (text or "").strip()
    if len(body) < _MIN_CHARS or "```" in body:
        return text
    starts = _sentence_starts(body)
    if len(starts) < 2:
        return text
    opening = body[: starts[1]].strip()
    if len(opening) < _MIN_OPENING_CHARS:
        return text
    opening_words = _words(opening)
    low, high = _LENGTH_RATIO
    for i, start in enumerate(starts[1:], start=1):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        if _similarity(opening_words, _words(body[start:end])) < _MIN_SIMILARITY:
            continue
        first, second = body[:start].rstrip(), body[start:].strip()
        if low <= len(second) / max(len(first), 1) <= high:
            logger.info(
                "Dropped a reworded repeat from the agent reply",
                extra={"data": {"kept_chars": len(first), "dropped_chars": len(second)}},
            )
            return first
    return text
