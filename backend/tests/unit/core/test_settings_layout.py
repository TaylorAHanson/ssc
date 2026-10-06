"""Admin → Settings layout: sections, capabilities, requirements, retired keys."""
import copy
import logging

import pytest

from app.core import settings_store as ss
from app.core.config import _yaml_config, settings
from app.core.default_config import DEFAULT_CONFIG


@pytest.fixture
def live_config():
    """Snapshot and restore the live config around a test that mutates it."""
    snapshot = copy.deepcopy(_yaml_config)
    yield _yaml_config
    _yaml_config.clear()
    _yaml_config.update(snapshot)


def _section_groups():
    return [g for s in ss.SECTIONS for g in s["groups"]]


def _key_exists_in_defaults(key: str) -> bool:
    if key.startswith("features."):
        return key[len("features."):] in DEFAULT_CONFIG["features"]
    if key.startswith("ui.tabs."):
        return key[len("ui.tabs."):] in DEFAULT_CONFIG["ui"]["tabs"]
    return False


# --- layout ---------------------------------------------------------------

def test_every_group_is_in_exactly_one_section():
    placed = _section_groups()
    assert len(placed) == len(set(placed)), "a group is listed in more than one section"
    for group in {f["group"] for f in ss.EDITABLE_FIELDS}:
        assert placed.count(group) == 1, f"{group!r} is not in exactly one section"


def test_every_section_group_has_content():
    special = {ss.FEATURES_GROUP, ss.ROLES_GROUP}
    field_groups = {f["group"] for f in ss.EDITABLE_FIELDS}
    for group in _section_groups():
        assert group in special or group in field_groups, f"{group!r} has no fields"


def test_group_descriptions_name_real_groups():
    placed = set(_section_groups())
    for group in ss.GROUP_DESCRIPTIONS:
        assert group in placed, f"description for unknown group {group!r}"


def test_group_requires_name_real_groups():
    placed = set(_section_groups())
    for group in ss.GROUP_REQUIRES:
        assert group in placed


def test_section_layout():
    assert [(s["title"], s["groups"]) for s in ss.SECTIONS] == [
        ("General", ["Appearance", ss.FEATURES_GROUP, ss.ROLES_GROUP, "Notifications"]),
        ("Discover & Analyze", ["Agent", "Data Catalog"]),
        ("Requests & Approvals", ["Group Management (LMWS)", "App Code Review"]),
        ("Learn & Share", ["Links & Embedded Apps", "Calendar"]),
        ("Watch Tower", ["OmniGuard", "Target Workspaces", "Data Certification", "Metadata Manager"]),
        ("Control Tower", ["Workflow Studio", "Preview Features"]),
        ("Platform", ["Infrastructure"]),
    ]


def test_target_workspaces_is_not_gated():
    # Data Certification also reads the target-workspace list, so it must stay
    # editable even while OmniGuard is off.
    assert "Target Workspaces" not in ss.GROUP_REQUIRES
    by_key = {f["key"]: f for f in ss.get_state()["fields"]}
    for key in ("TARGET_WORKSPACE_SP_SECRET_SCOPE", "collection:target_workspaces"):
        assert by_key[key]["group"] == "Target Workspaces"
        assert by_key[key]["requires"] == []


def test_removed_groups_are_gone():
    placed = set(_section_groups())
    for gone in ("Scheduling", "System Banner", "Data & AI"):
        assert gone not in placed


def test_requires_keys_exist_in_defaults():
    keys = [k for f in ss.EDITABLE_FIELDS for k in (f.get("requires") or [])]
    keys += [k for reqs in ss.GROUP_REQUIRES.values() for k in reqs]
    for key in keys:
        assert _key_exists_in_defaults(key), f"requires {key!r} is not a default flag/tab"


def test_get_state_resolves_group_and_field_requires():
    by_key = {f["key"]: f for f in ss.get_state()["fields"]}
    assert by_key["EVENT_SYNC_CRON"]["requires"] == ["features.calendar"]
    assert by_key["CONTRACT_SYNC_CRON"]["requires"] == ["features.governance", "features.data_discovery"]
    assert by_key["SCAN_CATALOGS"]["requires"] == []


# --- capabilities ---------------------------------------------------------

def test_capability_keys_exist_in_defaults():
    for cap in ss.CAPABILITIES:
        if cap.get("feature"):
            assert cap["feature"] in DEFAULT_CONFIG["features"], cap["id"]
        for t in ss._capability_tabs(cap):
            assert t["name"] in DEFAULT_CONFIG["ui"]["tabs"], (cap["id"], t["name"])
        assert cap["section"] in ss.CAPABILITY_SECTIONS, cap["id"]


def test_capability_ids_are_unique():
    ids = [c["id"] for c in ss.CAPABILITIES]
    assert len(ids) == len(set(ids))


def test_each_flag_and_tab_is_claimed_at_most_once():
    features = [c["feature"] for c in ss.CAPABILITIES if c.get("feature")]
    tabs = [t["name"] for c in ss.CAPABILITIES for t in ss._capability_tabs(c)]
    assert len(features) == len(set(features))
    assert len(tabs) == len(set(tabs))


def test_unmapped_flag_and_tab_land_in_other(live_config):
    live_config.setdefault("features", {})["brand_new_flag"] = True
    live_config.setdefault("ui", {}).setdefault("tabs", {})["brand_new_tab"] = True

    rows = {r["id"]: r for r in ss.capability_state()}
    assert rows["feature:brand_new_flag"]["section"] == ss.OTHER_SECTION
    assert rows["feature:brand_new_flag"]["primary"] == "features.brand_new_flag"
    assert rows["tab:brand_new_tab"]["section"] == ss.OTHER_SECTION
    assert rows["tab:brand_new_tab"]["primary"] == "ui.tabs.brand_new_tab"


def test_every_live_flag_and_tab_appears_once(live_config):
    keys = [k for r in ss.capability_state() for k in r["keys"]]
    assert len(keys) == len(set(keys))
    expected = {f"features.{n}" for n in live_config.get("features") or {}}
    expected |= {f"ui.tabs.{n}" for n in (live_config.get("ui") or {}).get("tabs") or {}}
    assert set(keys) == expected


def test_effective_ui_tabs_hides_tabs_of_disabled_feature(live_config):
    live_config["features"]["sentinel"] = True
    live_config["ui"]["tabs"]["sentinel"] = True
    assert ss.effective_ui_tabs()["sentinel"] is True

    live_config["features"]["sentinel"] = False
    assert ss.effective_ui_tabs()["sentinel"] is False
    # The stored tab value is untouched so turning the feature back on restores it.
    assert live_config["ui"]["tabs"]["sentinel"] is True


# --- retired settings -----------------------------------------------------

class _Row:
    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.updated_by = None


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return None


class _FakeDb:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.committed = False

    def query(self, _model):
        return _Query(self.rows)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.committed = True


def test_retired_keys_are_not_editable():
    editable = {f["key"] for f in ss.EDITABLE_FIELDS}
    assert not (editable & ss.RETIRED_KEYS)
    assert "self_service_center" not in DEFAULT_CONFIG
    assert "self_service" not in DEFAULT_CONFIG["features"]
    assert "algolia" not in (DEFAULT_CONFIG.get("web_search") or {})


def test_load_overrides_skips_retired_rows(live_config, caplog):
    db = _FakeDb([
        _Row("yaml:self_service_center", {"enabled": True, "categories": []}),
        _Row("features.self_service", True),
        _Row("yaml:web_search.algolia.api_key", "secret"),
        _Row("yaml:links.genie_full_experience_url", "https://example.com/genie"),
        _Row("yaml:tools.ask_your_data.default_genie_space_id", "abc"),
        _Row("features.skills", False),
    ])
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        applied = ss.load_overrides(db)

    assert applied == 1
    assert live_config["features"]["skills"] is False
    assert "self_service" not in live_config["features"]
    assert "self_service_center" not in live_config
    assert live_config["links"]["genie_full_experience_url"] == DEFAULT_CONFIG["links"]["genie_full_experience_url"]
    retired_logs = [r for r in caplog.records if "retired" in r.getMessage()]
    assert len(retired_logs) == 1
    assert retired_logs[0].levelno == logging.INFO
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_set_many_drops_retired_keys(live_config):
    db = _FakeDb()
    state = ss.set_many(db, {"features.self_service": True, "features.skills": False})

    assert [r.key for r in db.added] == ["features.skills"]
    assert db.committed
    assert "self_service" not in live_config["features"]
    assert "sections" in state and "capabilities" in state


def test_validate_key_rejects_retired_keys():
    for key in ss.RETIRED_KEYS:
        with pytest.raises(ValueError):
            ss._validate_key(key)


def test_web_search_config_has_no_algolia():
    assert "algolia" not in settings.web_search_config()
