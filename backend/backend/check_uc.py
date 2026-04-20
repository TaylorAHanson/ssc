import os
from dotenv import load_dotenv

load_dotenv()

from databricks.sdk import WorkspaceClient

w = WorkspaceClient(
    host=os.environ.get("DATABRICKS_HOST"),
    client_id=os.environ.get("DATABRICKS_CLIENT_ID"),
    client_secret=os.environ.get("DATABRICKS_CLIENT_SECRET"),
    auth_type="oauth-m2m"
)

if "DATABRICKS_CONFIG_PROFILE" in os.environ:
    del os.environ["DATABRICKS_CONFIG_PROFILE"]

res = w.statement_execution.execute_statement(
    statement="SELECT table_name FROM taylor_hanson_build_catalog.information_schema.table_tags WHERE tag_name = 'certification_eligible' AND tag_value = 'true'",
    warehouse_id=os.environ.get("DATABRICKS_WAREHOUSE_ID"),
    wait_timeout="30s"
)
if res.result and res.result.data_array:
    for row in res.result.data_array:
        print(row[0])
else:
    print("No tables found")
