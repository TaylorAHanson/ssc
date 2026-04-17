from app.db.session import get_lakebase_session
from app.db.data_asset import DataAssetModel
from app.db.data_contract import DataContractModel

def update_urls():
    db = get_lakebase_session()
    contracts = db.query(DataContractModel).all()
    count = 0
    for c in contracts:
        asset = db.query(DataAssetModel).filter(DataAssetModel.id == c.dataset_id).first()
        if asset:
            asset.contract_url = f"/governance/certification?dataset={c.dataset_id}"
            count += 1
            print(f"Updated {asset.id}")
            
    db.commit()
    db.close()
    print(f"Updated {count} assets")

if __name__ == "__main__":
    update_urls()
