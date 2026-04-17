import asyncio
from app.providers.databricks.client import DatabricksProvider
from app.core.config import settings

async def main():
    provider = DatabricksProvider(
        host=settings.DATABRICKS_HOST,
        token=settings.DATABRICKS_TOKEN,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET
    )
    res = await provider.execute_sql(
        "DESCRIBE system.information_schema.tables",
        warehouse=settings.DATABRICKS_WAREHOUSE_ID
    )
    for row in res.get("rows", []):
        print(row)

asyncio.run(main())
