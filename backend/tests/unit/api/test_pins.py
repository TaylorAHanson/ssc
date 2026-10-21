"""Pinned Discover assets: idempotent pinning, per-user isolation, unpin, cap."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1 import pins as routes
from app.db.data_asset import DataAssetModel
from app.db.user_pin import UserPinModel
from app.services import user_pin_service as svc

ALICE = "alice@example.com"
BOB = "bob@example.com"


def _user(email: str):
    return SimpleNamespace(email=email, full_name="T", roles=[], entitlements=[])


def _pin(db, email, asset_id, asset_type="view", title=None, **extra):
    body = routes.PinCreate(asset_id=asset_id, asset_type=asset_type, title=title or asset_id, **extra)
    return routes.pin_asset(body=body, db=db, current_user=_user(email))


def _listed_ids(db, email):
    return [p["asset_id"] for p in routes.list_pins(db=db, current_user=_user(email))["pins"]]


def test_pinning_twice_keeps_one_row_and_refreshes_labels(db_session):
    _pin(db_session, ALICE, "main.sales.orders", title="orders")
    again = _pin(db_session, ALICE, "main.sales.orders", title="Orders (renamed)", subtitle="main.sales")

    assert db_session.query(UserPinModel).count() == 1
    assert again["title"] == "Orders (renamed)"
    assert again["subtitle"] == "main.sales"


def test_list_is_newest_first(db_session):
    _pin(db_session, ALICE, "a")
    _pin(db_session, ALICE, "b")
    first = db_session.query(UserPinModel).filter_by(asset_id="a").one()
    first.created_at = datetime(2020, 1, 1)
    db_session.commit()

    assert _listed_ids(db_session, ALICE) == ["b", "a"]


def test_pins_are_scoped_to_the_caller(db_session):
    _pin(db_session, ALICE, "shared.asset.id")
    _pin(db_session, ALICE, "alice.only")
    _pin(db_session, BOB, "shared.asset.id")

    assert sorted(_listed_ids(db_session, ALICE)) == ["alice.only", "shared.asset.id"]
    assert _listed_ids(db_session, BOB) == ["shared.asset.id"]


def test_one_user_cannot_unpin_anothers_pin(db_session):
    _pin(db_session, ALICE, "x")

    assert routes.unpin_asset(asset_id="x", db=db_session, current_user=_user(BOB)) == {"deleted": 0}
    assert _listed_ids(db_session, ALICE) == ["x"]


def test_owner_key_is_case_insensitive(db_session):
    _pin(db_session, "Alice@Example.com", "x")
    _pin(db_session, ALICE, "x")

    assert db_session.query(UserPinModel).one().user_email == ALICE
    assert _listed_ids(db_session, "ALICE@EXAMPLE.COM") == ["x"]


def test_unpin_removes_and_is_idempotent(db_session):
    _pin(db_session, ALICE, "x")
    _pin(db_session, ALICE, "y")

    assert routes.unpin_asset(asset_id="x", db=db_session, current_user=_user(ALICE)) == {"deleted": 1}
    assert routes.unpin_asset(asset_id="x", db=db_session, current_user=_user(ALICE)) == {"deleted": 0}
    assert _listed_ids(db_session, ALICE) == ["y"]


def test_cap_rejects_new_pins_with_a_clear_message(db_session):
    for asset_id in ("a", "b"):
        svc.pin_asset(db_session, ALICE, asset_id=asset_id, asset_type="view", title=asset_id, max_pins=2)

    with pytest.raises(svc.PinLimitError, match="up to 2"):
        svc.pin_asset(db_session, ALICE, asset_id="c", asset_type="view", title="c", max_pins=2)

    # Re-pinning an existing asset at the cap is still fine.
    svc.pin_asset(db_session, ALICE, asset_id="a", asset_type="view", title="A", max_pins=2)
    # Other users have their own budget.
    svc.pin_asset(db_session, BOB, asset_id="c", asset_type="view", title="c", max_pins=2)


def test_route_turns_the_cap_into_a_400(db_session):
    for i in range(svc.max_pins_per_user()):
        svc.pin_asset(db_session, ALICE, asset_id=f"t{i}", asset_type="view", title=f"t{i}")

    with pytest.raises(HTTPException) as exc:
        _pin(db_session, ALICE, "one.too.many")
    assert exc.value.status_code == 400
    assert str(svc.max_pins_per_user()) in exc.value.detail


def test_availability_flags_assets_missing_from_the_catalog(db_session):
    db_session.add(DataAssetModel(id="main.sales.orders", catalog="main", schema="sales", table_name="orders", type="VIEW"))
    db_session.commit()
    _pin(db_session, ALICE, "main.sales.orders")
    _pin(db_session, ALICE, "main.sales.gone")
    _pin(db_session, ALICE, "dash-1", asset_type="dashboard", url="https://example.com/d/1")

    listed = {p["asset_id"]: p["available"] for p in routes.list_pins(db=db_session, current_user=_user(ALICE))["pins"]}
    assert listed == {"main.sales.orders": True, "main.sales.gone": False, "dash-1": None}


def test_only_web_links_are_accepted():
    with pytest.raises(ValidationError):
        routes.PinCreate(asset_id="d", asset_type="dashboard", title="d", url="javascript:alert(1)")
    assert routes.PinCreate(asset_id="d", asset_type="dashboard", title="d", url="  ").url is None
