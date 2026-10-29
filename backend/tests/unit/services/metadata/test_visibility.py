"""What a caller gets back about objects outside its browse scope (ADR-0006 D4, browsing)."""
import pytest

from app.services.metadata import changes, visibility
from app.services.metadata.authz import BrowseScope
from app.services.metadata.errors import Forbidden, NotFound
from app.services.metadata.types import (
    AuthzDecision,
    ChangeEvent,
    ChangeItem,
    ChangePreview,
    ChangeSet,
    Finding,
    ItemDecision,
    VersionConflictItem,
)

MINE, HIDDEN = "sales_dev.crm.customers", "hr_dev.people.staff"
OWNER = BrowseScope(level="owned", owned=["sales_dev.crm"])
FULL = BrowseScope(level="full")


def _decision(fqn_key, *, before="people_secret", kept=False, sensitive=False, exists=True,
              key="domain") -> ItemDecision:
    item = ChangeItem(fqn_key=fqn_key, name=fqn_key, securable_type="table", field="tag", key=key,
                      action="set", before=before, before_exists=True, before_version=4, after="hr",
                      object_exists=exists, unchanged_in_uc=kept)
    reasons = ["Unity Catalog already has domain=people_secret."] if kept else ["No owner."]
    decision = AuthzDecision(allowed=not sensitive, needs_approval=sensitive, can_request=True, reasons=reasons,
                             sensitive_reasons=["Changing domain from people_secret."] if sensitive else [])
    return ItemDecision(item=item, decision=decision)


def _preview(*decisions, findings=()) -> ChangePreview:
    items = list(decisions)
    return ChangePreview(items=items, findings=list(findings), digest=changes.items_digest([d.item for d in items]),
                         report_markdown="people_secret")


def test_hidden_items_lose_their_current_values():
    preview = _preview(_decision(MINE, before="crm"), _decision(HIDDEN, kept=True, sensitive=True),
                       findings=[Finding(code="changed_since", severity="warn", fqn_key=HIDDEN,
                                         message="people_secret was changed again after this change.")])

    out = visibility.redact_preview(preview, OWNER, reason="Rename")

    mine, hidden = out.items
    assert mine.item.before == "crm" and mine.item.before_hidden is False
    assert (hidden.item.before, hidden.item.before_exists, hidden.item.before_version) == (None, False, 0)
    assert hidden.item.before_hidden is True and hidden.item.unchanged_in_uc is False
    assert out.findings == []
    assert "people_secret" not in out.model_dump_json()
    assert out.digest != preview.digest
    assert preview.items[1].item.before == "people_secret"


def test_kept_values_of_hidden_objects_are_left_out_and_the_counts_follow():
    preview = _preview(_decision(HIDDEN, exists=False), _decision(HIDDEN, kept=True, exists=False, key="secret"),
                       _decision(MINE, before="crm", kept=True, exists=False, key="tier"))
    preview.direct_count, preview.kept_count = 3, 2

    out = visibility.redact_preview(preview, OWNER)

    assert [(d.item.fqn_key, d.item.key) for d in out.items] == [(HIDDEN, "domain"), (MINE, "tier")]
    assert (out.direct_count, out.kept_count, out.approval_count, out.denied_count) == (2, 1, 0, 0)
    assert '"key":"secret"' not in out.model_dump_json()


def test_nothing_is_copied_when_nothing_is_hidden():
    preview = _preview(_decision(MINE))

    assert visibility.redact_preview(preview, OWNER) is preview
    assert visibility.redact_preview(_preview(_decision(HIDDEN)), FULL).items[0].item.before == "people_secret"


def test_the_approval_digest_ignores_the_hidden_flag():
    item = _decision(MINE).item

    assert changes.items_digest([item]) == changes.items_digest([item.model_copy(update={"before_hidden": True})])


def test_conflicts_on_hidden_objects_lose_the_current_value():
    conflicts = [VersionConflictItem(fqn_key=k, name=k, key="domain", expected_version=1, current_version=2,
                                     current_value="v") for k in (MINE, HIDDEN)]

    out = visibility.redact_conflicts(conflicts, OWNER)

    assert [c.current_value for c in out] == ["v", None]


def _change_set(*fqn_keys) -> ChangeSet:
    return ChangeSet(id="cs-1", actor="admin", source="ui", events=[
        ChangeEvent(id=i, change_set_id="cs-1", object_id=f"o{i}", fqn_key=k, action="tag_set", source="ui")
        for i, k in enumerate(fqn_keys)])


def test_change_sets_show_only_visible_events():
    mixed = _change_set(MINE, HIDDEN)

    assert [e.fqn_key for e in visibility.visible_change_set(mixed, OWNER).events] == [MINE]
    with pytest.raises(NotFound, match="doesn't exist"):
        visibility.visible_change_set(_change_set(HIDDEN), OWNER)
    with pytest.raises(Forbidden, match="only governance"):
        visibility.require_whole_change_set(mixed, OWNER)
    visibility.require_whole_change_set(_change_set(MINE), OWNER)
    visibility.require_whole_change_set(mixed, FULL)
