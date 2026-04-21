import asyncio
import sys
from app.providers.databricks.client import DatabricksProvider
from app.providers.databricks.handlers import DatasetResourceHandler
from app.core.config import settings

async def main():
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST, 
            client_id=settings.DATABRICKS_CLIENT_ID, 
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        handler = DatasetResourceHandler(provider.client)
        resources = await handler.discover()
        for r in resources:
            print(f"Discovered: {r}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
