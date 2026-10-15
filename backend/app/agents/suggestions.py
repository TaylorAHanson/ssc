"""
Next-step suggestions: the clickable replies shown under the latest agent message.

The model ends a reply with a fenced block tagged ``suggestions`` holding a JSON
array of short strings, written in the user's voice (see
``NEXT_STEP_SUGGESTIONS_INSTRUCTIONS`` in ``prompts.py``)::

    ```suggestions
    ["Find the table owner", "Show similar tables"]
    ```

The runner splits that block off the final message before anything reaches the
user, so the chat bubble, the stored transcript and the downloaded transcript
only ever hold prose. The parsed list travels separately on ``MessageEvent``.

Clicking a chip sends its text verbatim as the user's next message, so every item
is reduced to short plain text here: a chip that renders markdown or carries a
link would be sending the user's words with someone else's formatting.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, List, Optional, Tuple

from app.core.feature_flags import is_feature_enabled

logger = logging.getLogger(__name__)

FEATURE_FLAG = "next_step_suggestions"
BLOCK_TAG = "suggestions"
MAX_SUGGESTIONS = 4
# The prompt asks for under 60 characters. Longer items are dropped rather than
# cut, because a truncated chip would send a broken sentence as the user's message.
MAX_SUGGESTION_CHARS = 80

_COMPLETE_BLOCK = re.compile(
    r"```[ \t]*" + BLOCK_TAG + r"\b[ \t]*\r?\n?(?P<body>.*?)```[ \t]*",
    re.IGNORECASE | re.DOTALL,
)
# A block the model started but never closed (output cut off by the token cap).
_OPEN_BLOCK = re.compile(r"```[ \t]*" + BLOCK_TAG + r"\b.*\Z", re.IGNORECASE | re.DOTALL)

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]{0,200}>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_LEADING_MARKER = re.compile(r"^(?:[-*•>]+|#{1,6}|\d{1,2}[.)])\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_QUOTES = "\"'“”‘’"
_ITEM_TEXT_KEYS = ("text", "prompt", "label", "title")


def suggestions_enabled() -> bool:
    return is_feature_enabled(FEATURE_FLAG)


def _sanitize_one(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        raw = next(
            (raw[k] for k in _ITEM_TEXT_KEYS if isinstance(raw.get(k), str) and raw[k].strip()),
            None,
        )
    if not isinstance(raw, str):
        return None
    text = _CONTROL_CHARS.sub(" ", raw)
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    if _URL.search(text):
        return None
    text = text.replace("`", "").replace("*", "")
    text = " ".join(text.split())
    text = _LEADING_MARKER.sub("", text)
    text = text.strip().strip(_QUOTES).strip()
    if not text or len(text) > MAX_SUGGESTION_CHARS:
        return None
    return text


def sanitize_suggestions(items: Optional[Iterable[Any]]) -> List[str]:
    """Plain-text, de-duplicated, capped list. Anything unusable is dropped."""
    if not items or isinstance(items, (str, bytes, dict)):
        return []
    out: List[str] = []
    seen = set()
    for item in items:
        text = _sanitize_one(item)
        if text is None:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def _parse_body(body: str) -> List[str]:
    text = body.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            logger.debug("Ignoring malformed suggestions block: %r", text[:200])
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            logger.debug("Ignoring malformed suggestions block: %r", text[:200])
            return []
    if isinstance(data, dict):
        data = data.get(BLOCK_TAG)
    if not isinstance(data, list):
        logger.debug("Ignoring suggestions block that is not a list: %r", text[:200])
        return []
    return sanitize_suggestions(data)


def split_suggestions(text: Optional[str]) -> Tuple[str, List[str]]:
    """Return ``(text without any suggestions block, parsed suggestions)``.

    Every complete block is removed and the last one is parsed. An unterminated
    block at the end is removed too, with nothing parsed from it. Text with no
    block comes back unchanged.
    """
    if not text:
        return text or "", []
    blocks = list(_COMPLETE_BLOCK.finditer(text))
    cleaned = _COMPLETE_BLOCK.sub("", text) if blocks else text
    open_block = _OPEN_BLOCK.search(cleaned)
    if open_block:
        cleaned = cleaned[: open_block.start()]
    if not blocks and not open_block:
        return text, []
    cleaned = cleaned.strip()
    suggestions = _parse_body(blocks[-1].group("body")) if blocks else []
    return cleaned, suggestions


def with_suggestions_block(content: Optional[str], suggestions: Optional[Iterable[Any]]) -> str:
    """An assistant turn as the model should see it when history is replayed.

    Stored transcripts keep prose and suggestions apart. Putting the block back
    (one canonical copy, never a stale raw one) keeps the model's view of its own
    replies in the format it is asked to produce, and shows it which offer a
    clicked reply came from. Without the feature, the model gets prose only.
    """
    text, _ = split_suggestions(content or "")
    items = sanitize_suggestions(suggestions) if suggestions_enabled() else []
    if not items:
        return text
    block = json.dumps(items, ensure_ascii=False)
    return f"{text}\n\n```{BLOCK_TAG}\n{block}\n```" if text else f"```{BLOCK_TAG}\n{block}\n```"


__all__ = [
    "BLOCK_TAG",
    "FEATURE_FLAG",
    "MAX_SUGGESTIONS",
    "MAX_SUGGESTION_CHARS",
    "sanitize_suggestions",
    "split_suggestions",
    "suggestions_enabled",
    "with_suggestions_block",
]
