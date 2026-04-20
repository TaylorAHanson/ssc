import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.db.session import get_lakebase_session
from app.db.data_asset import DataAssetModel

db = get_lakebase_session()
assets = db.query(DataAssetModel).filter(DataAssetModel.table_name.in_(['sales_data', 'customer_retention_test'])).all()
for a in assets:
    print(f"[{a.table_name}] Certified: {a.certified}, Contract_URL: {a.contract_url}, Tags: {a.tags}")
    
db.close()
