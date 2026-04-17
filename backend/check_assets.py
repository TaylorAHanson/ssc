from app.db.session import get_lakebase_session
from app.db.data_asset import DataAssetModel
from app.db.data_contract import DataContractModel

db = get_lakebase_session()
contracts = db.query(DataContractModel).all()

for c in contracts:
    asset = db.query(DataAssetModel).filter(DataAssetModel.id == c.dataset_id).first()
    if not asset:
        parts = c.dataset_id.split(".")
        catalog = parts[0]
        schema = parts[1] if len(parts) > 1 else "default"
        table = parts[2] if len(parts) > 2 else "unknown"
        
        new_asset = DataAssetModel(
            id=c.dataset_id,
            catalog=catalog,
            schema=schema,
            table_name=table,
            type="TABLE",
            description=f"Mock asset for {c.dataset_id}",
            domain="sales",
            contract_url=f"/governance/certification?dataset={c.dataset_id}"
        )
        db.add(new_asset)
        print(f"Created mock asset for {c.dataset_id}")
    else:
        if not asset.contract_url:
            asset.contract_url = f"/governance/certification?dataset={c.dataset_id}"
            db.add(asset)
            print(f"Updated contract URL for {c.dataset_id}")

db.commit()
db.close()
print("Done")