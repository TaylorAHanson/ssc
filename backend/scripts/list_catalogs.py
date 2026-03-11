import asyncio
import os
import sys

# Add the backend directory to sys.path
backend_dir = "/Users/taylor.hanson/qc-selfservice-v3/backend"
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Try to load .env manually
env_path = os.path.join(backend_dir, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip().strip("'\"")

from app.providers.databricks.client import DatabricksProvider

async def list_cats():
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    
    if not host or not token:
        print("ERROR: DATABRICKS_HOST or TOKEN not found")
        return

    provider = DatabricksProvider(host=host, token=token)
    try:
        # We can use execute_sql to list catalogs if the SDK method is unknown
        results = await provider.execute_sql("SHOW CATALOGS")
        print("Available Catalogs:")
        for row in results.get("rows", []):
            print(f"- {row[0]}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(list_cats())
