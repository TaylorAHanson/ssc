from app.db.session import get_engine
from app.db.data_asset import DataAssetModel
engine = get_engine()
DataAssetModel.__table__.drop(engine, checkfirst=True)
DataAssetModel.__table__.create(engine, checkfirst=True)
print("Table dropped and recreated")
