"""Keyword matching shared by the agent's catalog search and the Discover page.

A query becomes a list of term groups: one group per meaningful word, holding
that word's stem. An asset matches when ANY group hits (OR), and ranks by how
many groups hit and where. Stems only strip suffixes, so a stem is
always a prefix of the word and substring matching still finds the word itself
("deliveries" and "delivery" both stem to "deliver").

The frontend mirrors these rules in ``src/lib/catalogSearch.ts``; keep the two in step.
"""
from __future__ import annotations

import re
from typing import List, Optional

STOPWORDS = frozenset({
    "a", "about", "all", "an", "and", "any", "are", "by", "data", "dataset", "datasets", "do", "find",
    "for", "from", "get", "have", "i", "in", "info", "information", "is", "list", "me", "my", "of", "on",
    "or", "show", "table", "tables", "that", "the", "this", "to", "we", "what", "where", "which", "with",
})

_SUFFIXES = ("ies", "ing", "es", "ed", "s", "y")
_MIN_STEM = 4
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def words(text: Optional[str]) -> List[str]:
    """Lowercase words of ``text``; underscores, dots and dashes separate words."""
    return [w for w in _WORD_SPLIT.split((text or "").lower()) if w]


def stem(word: str) -> str:
    """Strip one common English suffix, keeping at least four letters."""
    w = word.lower()
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= _MIN_STEM:
            return w[: -len(suffix)]
    return w


def tokenize(query: Optional[str]) -> List[str]:
    """Meaningful query words: two letters or more, stopwords dropped."""
    return [w for w in words(query) if len(w) >= 2 and w not in STOPWORDS]


def term_groups(query: Optional[str]) -> List[List[str]]:
    """One group of forms per distinct query word (currently just its stem)."""
    return [[root] for root in dict.fromkeys(stem(t) for t in tokenize(query))]


def normalized(text: Optional[str]) -> str:
    """``text`` as space-separated lowercase words, for matching forms and phrases."""
    return " " + " ".join(words(text)) + " "


def form_in(form: str, haystack: str) -> bool:
    """Whether a word in a ``normalized`` haystack starts with ``form``."""
    return f" {form}" in haystack


# Shorter forms inside a word are mostly coincidence ("late" in "unrelated").
INSIDE_WORD_MIN = 5
INSIDE_WORD_WEIGHT = 0.5


def form_weight(form: str, haystack: str) -> float:
    """1 for a word starting with ``form``; half for one containing it (5+ letters); else 0."""
    if form_in(form, haystack):
        return 1.0
    if len(form) >= INSIDE_WORD_MIN and form in haystack:
        return INSIDE_WORD_WEIGHT
    return 0.0


def group_weight(forms: List[str], haystack: str) -> float:
    """A query word's best hit across its forms."""
    return max((form_weight(f, haystack) for f in forms), default=0.0)


def like_pattern(form: str) -> str:
    """A SQL LIKE pattern matching ``form`` anywhere."""
    return "%" + form.replace("%", "") + "%"
