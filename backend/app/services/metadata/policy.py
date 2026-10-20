"""Key policies, governed values and sensitivity rules (ADR-0006 D4). Workstream D.

``METADATA_KEY_POLICIES`` replaces the governance repo's ``tag_policy.yml``.
``tag_policy()`` adapts it to ``TagPolicy`` so lint and risk keep working.

Settings are read on every call, since admins edit them live. Key matching is
case-insensitive throughout.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Dict, List, Optional

from app.core.config import settings
from app.services.metadata.types import RELATION_TYPES, ChangeItem, Finding, KeyPolicy

logger = logging.getLogger(__name__)

_TRUE = frozenset({"true", "yes", "y", "1", "on"})
# A PII flag set to one of these no longer flags anything.
_FALSEY_FLAG = frozenset({"false", "no", "n", "0", "off", "none"})

# applies_to accepts levels (catalog, schema, relation, column) and securable types.
# "table" means any table-like object (tables and views), as in the old tag policy.
_ANY_RELATION = frozenset({"table", "relation"})
_APPLIES_TOKENS = frozenset({"catalog", "schema", "relation", "column", *RELATION_TYPES})
_KIND_NAMES = {
    "catalog": "catalogs",
    "schema": "schemas",
    "table": "tables and views",
    "relation": "tables and views",
    "view": "views",
    "materialized_view": "materialized views",
    "streaming_table": "streaming tables",
    "metric_view": "metric views",
    "column": "columns",
}
_warned_tokens: set = set()


def _as_list(value: object) -> List[str]:
    if not value:
        return []
    parts = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    return [str(p).strip() for p in parts if p is not None and str(p).strip()]


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return bool(value)


def _applies_token(raw: str) -> str:
    token = re.sub(r"[\s-]+", "_", raw.strip().lower())
    if token not in _APPLIES_TOKENS and token.endswith("s") and token[:-1] in _APPLIES_TOKENS:
        token = token[:-1]
    if token not in _APPLIES_TOKENS and token not in _warned_tokens:
        _warned_tokens.add(token)
        logger.warning("Metadata key policy applies_to has an unknown kind %r; it matches nothing.", raw)
    return token


def key_policies() -> List[KeyPolicy]:
    """``METADATA_KEY_POLICIES`` rows in order; rows without a key and repeated keys are skipped."""
    out: List[KeyPolicy] = []
    seen = set()
    for raw in settings.METADATA_KEY_POLICIES or []:
        if isinstance(raw, KeyPolicy):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(KeyPolicy(
            key=key,
            applies_to=list(dict.fromkeys(_applies_token(t) for t in _as_list(raw.get("applies_to")))),
            allowed_values=list(dict.fromkeys(_as_list(raw.get("allowed_values")))),
            pattern=str(raw.get("pattern") or "").strip() or None,
            editor_groups=list(dict.fromkeys(_as_list(raw.get("editor_groups")))),
            sensitive=_as_bool(raw.get("sensitive")),
            required=_as_bool(raw.get("required")),
            description=str(raw.get("description") or "").strip() or None,
        ))
    return out


def policy_for(key: str) -> Optional[KeyPolicy]:
    wanted = (key or "").strip().lower()
    if not wanted:
        return None
    return next((p for p in key_policies() if p.key.lower() == wanted), None)


def exempt_keys() -> List[str]:
    """METADATA_EXEMPT_KEYS plus the claim tag key, lowercased.

    The claim key stays exempt with claims off: catalogs keep the markers other deployments wrote,
    and enforcement must not strip them.
    """
    keys = _as_list(settings.METADATA_EXEMPT_KEYS)
    claim = str(settings.METADATA_CLAIM_TAG_KEY or "").strip()
    if claim:
        keys.append(claim)
    return list(dict.fromkeys(k.lower() for k in keys))


def is_exempt(key: str) -> bool:
    return (key or "").strip().lower() in exempt_keys()


def applies_to(policy: KeyPolicy, securable_type: str) -> bool:
    """Whether ``policy``'s key may be set on an object of this securable type (blank applies_to = all)."""
    if not policy.applies_to:
        return True
    kinds = set(policy.applies_to)
    return securable_type in kinds or (securable_type in RELATION_TYPES and bool(kinds & _ANY_RELATION))


@lru_cache(maxsize=512)
def _compile(pattern: str) -> Optional[re.Pattern]:
    try:
        return re.compile(pattern)
    except re.error as e:
        logger.warning("Metadata key policy pattern %r is not a valid regular expression (%s); not checked.",
                       pattern, e)
        return None


def _label(item: ChangeItem) -> str:
    return f"{item.name}.{item.column}" if item.column else item.name


def _where(kinds: List[str]) -> str:
    names = list(dict.fromkeys(_KIND_NAMES.get(k, k) for k in kinds))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _governed_values(governed: Optional[Dict[str, List[str]]], key: str) -> List[str]:
    if not governed:
        return []
    if key in governed:
        return list(governed[key] or [])
    wanted = key.lower()
    return next((list(v or []) for k, v in governed.items() if str(k).lower() == wanted), [])


def check_item(item: ChangeItem, *, governed: Optional[Dict[str, List[str]]] = None) -> List[Finding]:
    """Key mode, applies_to, pattern, allowed values, governed values and exempt keys for one item.

    Only tag items are checked. Removals are checked only for exempt and
    required keys, so tags that no longer fit the policy can always be cleaned up.
    A key-only tag (no value) is checked as the empty string against the
    pattern and allowed values, but not against governed values (Unity Catalog
    doesn't check those either).
    """
    if item.field != "tag" or not item.key:
        return []
    key = item.key
    label = _label(item)
    findings: List[Finding] = []

    def deny(code: str, message: str) -> List[Finding]:
        findings.append(Finding(code=code, severity="deny", message=f"{label}: {message}",
                                fqn_key=item.fqn_key, key=key))
        return findings

    if is_exempt(key):
        return deny("exempt_key", f"'{key}' is managed outside the metadata manager and can't be set or "
                                  "removed here.")
    pol = policy_for(key)

    if item.action == "unset":
        if pol is not None and pol.required and applies_to(pol, item.securable_type):
            deny("required_key", f"'{key}' is required, so it can't be removed. You can change its value.")
        return findings

    if pol is None:
        if str(settings.METADATA_KEY_MODE or "").strip().lower() == "known_only":
            known = ", ".join(p.key for p in key_policies()) or "(none)"
            deny("unknown_key", f"'{key}' isn't a known tag key. Allowed keys: {known}.")
            return findings
    else:
        if not applies_to(pol, item.securable_type):
            deny("not_applicable", f"'{key}' can only be set on {_where(pol.applies_to)}.")
        value = item.after if item.after is not None else ""
        compiled = _compile(pol.pattern) if pol.pattern else None
        if compiled is not None and not compiled.fullmatch(value):
            hint = f" {pol.description}" if pol.description else ""
            deny("pattern", f"'{value}' is not a valid value for '{key}'.{hint} It must match {pol.pattern}.")
        if pol.allowed_values and value not in pol.allowed_values:
            deny("not_allowed_value", f"'{value}' isn't an allowed value for '{key}'. Allowed values: "
                                      f"{', '.join(pol.allowed_values)}.")

    allowed = _governed_values(governed, key)
    if allowed and item.after is not None and item.after not in allowed:
        deny("governed_value", f"'{item.after}' isn't an allowed value for the governed tag '{key}'. "
                               f"Allowed values: {', '.join(allowed)}.")
    return findings


def _same_key(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def _classification_downgrade(item: ChangeItem) -> Optional[str]:
    levels = [str(v).strip().lower() for v in (settings.METADATA_CLASSIFICATION_LEVELS or []) if str(v).strip()]
    if not item.before_exists:
        return None
    before = item.before
    if item.action == "unset":
        return f"Removes the classification '{before}'." if before else "Removes the classification."
    rank = {v: i for i, v in enumerate(levels)}
    b = rank.get((before or "").strip().lower())
    if b is None:
        return None
    a = rank.get((item.after or "").strip().lower())
    if a is None:
        return f"Changes the classification from '{before}' to '{item.after or ''}', which isn't a known level."
    if a < b:
        return f"Lowers the classification from '{before}' to '{item.after}'."
    return None


def _removes_pii_flag(item: ChangeItem) -> Optional[str]:
    if not item.before_exists:
        return None
    if item.before is not None and item.before.strip().lower() in _FALSEY_FLAG:
        # It wasn't flagging anything.
        return None
    if item.action == "unset":
        return f"Removes the PII flag '{item.key}'."
    if item.after is not None and item.after.strip().lower() in _FALSEY_FLAG:
        return f"Sets the PII flag '{item.key}' to '{item.after}'."
    return None


def sensitive_reasons(item: ChangeItem) -> List[str]:
    """Why an item needs approval regardless of who makes it (empty = not sensitive).

    The environment rule (D5) isn't here: it depends on the item's environment,
    and ``authz.decide`` adds it.
    """
    if item.field != "tag" or not item.key:
        return []
    key = item.key
    reasons: List[str] = []
    if _same_key(key, settings.METADATA_OWNER_TAG_KEY):
        reasons.append(f"Changes the owner tag '{key}', which decides who may edit this object.")
    else:
        pol = policy_for(key)
        if pol is not None and pol.sensitive:
            reasons.append(f"'{key}' is a sensitive key.")
    if _same_key(key, settings.METADATA_CLASSIFICATION_KEY):
        downgrade = _classification_downgrade(item)
        if downgrade:
            reasons.append(downgrade)
    if any(_same_key(key, k) for k in _as_list(settings.METADATA_PII_KEYS)):
        removed = _removes_pii_flag(item)
        if removed:
            reasons.append(removed)
    return reasons


def tag_policy():
    """A ``workflows.tag_policy.TagPolicy`` built from the settings, for lint and risk.

    Exempt keys become reserved keys; ``known_only`` becomes the old ``strict``
    mode. A key with allowed values but no pattern gets a pattern built from
    them. Invalid patterns are left out. Key and value limits (Unity Catalog's)
    come from the built-in default policy.
    """
    from app.workflows.tag_policy import TagPolicy, get_default_policy

    known: Dict[str, Dict[str, object]] = {}
    for pol in key_policies():
        spec: Dict[str, object] = {"required": pol.required}
        if pol.description:
            spec["description"] = pol.description
        kinds = sorted({"table" if k in _ANY_RELATION or k in RELATION_TYPES else k
                        for k in pol.applies_to if k in _APPLIES_TOKENS})
        if kinds:
            spec["applies_to"] = kinds
        if pol.pattern and _compile(pol.pattern) is not None:
            spec["pattern"] = pol.pattern
        elif pol.allowed_values:
            spec["pattern"] = "(?:" + "|".join(re.escape(v) for v in pol.allowed_values) + ")"
        known[pol.key] = spec
    default = get_default_policy()
    known_only = str(settings.METADATA_KEY_MODE or "").strip().lower() == "known_only"
    return TagPolicy(
        reserved_keys=exempt_keys(),
        key_mode="strict" if known_only else "open",
        known_keys=known,
        protected_keys=[],
        key_pattern=default.key_pattern,
        max_tags_per_object=default.max_tags_per_object,
        max_key_length=default.max_key_length,
        max_value_length=default.max_value_length,
    )
