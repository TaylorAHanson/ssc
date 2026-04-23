import os
import sys
import json
from databricks.sdk import WorkspaceClient

def test_lineage():
    w = WorkspaceClient()
    try:
        resp = w.api_client.do("GET", "/api/2.0/lineage-tracking/table-lineage?table_name=main.default.my_table")
        print(json.dumps(resp, indent=2))
    except Exception as e:
        print(e)

if __name__ == "__main__":
    test_lineage()