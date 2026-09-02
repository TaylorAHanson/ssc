"""Client-side check of a tag change against the governance repo's tag policy.

The governance repo validates every migration against ``policy/tag_policy.yml``
and blocks the PR on a violation. That check is authoritative and stays. This
module runs the same rules at submit time so the requester sees the problem in
the UI, instead of the request being accepted and then quietly failing a check
on a pull request they may not be watching.

The policy is read from the repo rather than copied here — one source of truth,
and a policy edit takes effect without redeploying the app.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

POLICY_PATH = "policy/tag_policy.yml"

DEFAULT_POLICY_YAML = """
reserved_prefixes:
  - "system."
key_mode: open
known_keys:
  dataset:
    required: true
    description: Logical dataset this object belongs to. Groups tables and views.
    pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
  data_owner:
    required: true
    description: Accountable owner (team or group name) for the data.
    pattern: "^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,127}$"
  approver_group:
    required: true
    description: Group that approves access requests for this dataset.
    pattern: "^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,127}$"
  access_group:
    required: true
    description: Group granted access once a request is approved.
    pattern: "^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,127}$"
  reliability_window:
    required: true
    description: Freshness/reliability commitment, e.g. 24h or 7d.
    pattern: "^[0-9]+(m|h|d|w)$"
  classification:
    required: false
    description: Sensitivity classification.
    pattern: "^(public|internal|confidential|restricted)$"
protected_keys: []
limits:
  max_tags_per_object: 50
  max_key_length: 256
  max_value_length: 256
key_pattern: "^[A-Za-z0-9_][A-Za-z0-9._-]*$"
"""


def get_default_policy() -> "TagPolicy":
    return TagPolicy.parse(DEFAULT_POLICY_YAML)


@dataclass
class TagPolicy:
    reserved_prefixes: List[str] = field(default_factory=list)
    key_mode: str = "open"
    known_keys: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    protected_keys: List[str] = field(default_factory=list)
    key_pattern: Optional[str] = None
    max_tags_per_object: Optional[int] = None
    max_key_length: Optional[int] = None
    max_value_length: Optional[int] = None

    @classmethod
    def parse(cls, text: str) -> "TagPolicy":
        data = yaml.safe_load(text) or {}
        limits = data.get("limits") or {}
        return cls(
            reserved_prefixes=list(data.get("reserved_prefixes") or []),
            key_mode=(data.get("key_mode") or "open").strip().lower(),
            known_keys=dict(data.get("known_keys") or {}),
            protected_keys=list(data.get("protected_keys") or []),
            key_pattern=data.get("key_pattern"),
            max_tags_per_object=limits.get("max_tags_per_object"),
            max_key_length=limits.get("max_key_length"),
            max_value_length=limits.get("max_value_length"),
        )

    def _is_reserved(self, key: str) -> bool:
        return any(key.startswith(p) for p in self.reserved_prefixes)

    def is_reserved(self, key: str) -> bool:
        return self._is_reserved(key)

    def _is_protected(self, key: str) -> bool:
        """Protected keys may be re-valued but never removed."""
        if key in self.protected_keys:
            return True
        if key in ("dataset", "data_set"):
            return True
        return bool((self.known_keys.get(key) or {}).get("required"))

    def _check_set(self, table: str, key: str, value: str) -> List[str]:
        problems: List[str] = []
        if self._is_reserved(key):
            problems.append(
                f"{table}: '{key}' is a reserved tag — the Enforcement Sentinel owns "
                f"these and writes them directly."
            )
            return problems
        if self.key_pattern and not re.fullmatch(self.key_pattern, key):
            problems.append(f"{table}: tag key '{key}' contains characters that aren't allowed.")
        if self.max_key_length and len(key) > self.max_key_length:
            problems.append(
                f"{table}: tag key '{key}' is {len(key)} characters; the limit is "
                f"{self.max_key_length}."
            )
        if self.max_value_length and len(value) > self.max_value_length:
            problems.append(
                f"{table}: the value for '{key}' is {len(value)} characters; the limit "
                f"is {self.max_value_length}."
            )

        spec = self.known_keys.get(key)
        if spec is None:
            if self.key_mode == "strict":
                known = ", ".join(sorted(self.known_keys)) or "(none)"
                problems.append(
                    f"{table}: '{key}' is not a governed tag key. Allowed keys: {known}."
                )
            return problems

        pattern = spec.get("pattern")
        if pattern and not re.fullmatch(pattern, value):
            description = spec.get("description")
            hint = f" {description}" if description else ""
            problems.append(
                f"{table}: '{value}' is not a valid value for '{key}'.{hint} "
                f"It must match {pattern}."
            )
        return problems

    def _check_unset(self, table: str, key: str) -> List[str]:
        if self._is_reserved(key):
            return [
                f"{table}: '{key}' is a reserved tag and cannot be removed here — the "
                f"Enforcement Sentinel owns it."
            ]
        if self._is_protected(key):
            return [
                f"{table}: '{key}' is part of the certification contract and cannot be "
                f"removed. You can change its value, but not clear it."
            ]
        return []

    def check(
        self,
        changes: List[Dict[str, Any]],
        resulting_tag_counts: Optional[Dict[str, int]] = None,
    ) -> List[str]:
        """Return every policy problem in ``changes``; empty means it would pass.

        All problems are collected rather than raising on the first, so the user
        can fix the whole change in one pass instead of one error per submit.
        """
        problems: List[str] = []
        for change in changes:
            table = change.get("table") or "(unknown table)"
            for key, value in (change.get("set") or {}).items():
                problems.extend(self._check_set(table, key, str(value)))
            for key in change.get("unset") or []:
                problems.extend(self._check_unset(table, key))

            count = (resulting_tag_counts or {}).get(table)
            if self.max_tags_per_object and count and count > self.max_tags_per_object:
                problems.append(
                    f"{table}: would end up with {count} tags; Unity Catalog allows at "
                    f"most {self.max_tags_per_object} per object."
                )
        return problems


async def load_policy(provider, repo: str, ref: str) -> Optional[TagPolicy]:
    """Fetch and parse the governance repo's policy, or ``None`` if unavailable.

    Returning ``None`` deliberately lets the submit proceed: the repo's own
    validation still blocks a bad migration, so a GitHub hiccup or a repo without
    a policy file should degrade the error message, not the ability to file a
    request at all.
    """
    try:
        text = await provider.get_file_content(repo=repo, path=POLICY_PATH, ref=ref)
    except Exception as e:  # noqa: BLE001 - advisory check; the repo is the gate
        logger.warning("Could not read %s from %s@%s: %s", POLICY_PATH, repo, ref, e)
        return None
    if not text:
        logger.warning(
            "%s not found in %s@%s; skipping the client-side policy check", POLICY_PATH, repo, ref
        )
        return None
    try:
        return TagPolicy.parse(text)
    except yaml.YAMLError as e:
        logger.warning("Could not parse %s from %s@%s: %s", POLICY_PATH, repo, ref, e)
        return None
