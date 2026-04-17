import asyncio
from app.workers.tasks.sync_data_assets import sync_data_assets_task
asyncio.run(sync_data_assets_task(force=True))
