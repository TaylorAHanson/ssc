import asyncio
from app.db.session import get_lakebase_session
from app.db.data_contract import DataContractModel
from app.db.data_asset import DataAssetModel

db = get_lakebase_session()
contracts = db.query(DataContractModel).all()
for c in contracts:
    print(f"Contract: {c.dataset_id} (active: {c.is_active})")

assets = db.query(DataAssetModel).all()
for a in assets:
    print(f"Asset: {a.id}")
