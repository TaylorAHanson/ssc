import os
import time
from dotenv import load_dotenv
from databricks.sdk import WorkspaceClient
import datetime
import random

load_dotenv()

w = WorkspaceClient(
    host=os.environ.get("DATABRICKS_HOST"),
    client_id=os.environ.get("DATABRICKS_CLIENT_ID"),
    client_secret=os.environ.get("DATABRICKS_CLIENT_SECRET"),
    auth_type="oauth-m2m"
)

warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")

table_name = "taylor_hanson_build_catalog.main.adoc_dq_history"

drop_sql = f"DROP TABLE IF EXISTS {table_name}"
print(f"Executing: {drop_sql}")
w.statement_execution.execute_statement(statement=drop_sql, warehouse_id=warehouse_id, wait_timeout="30s")

create_sql = f"""
CREATE TABLE {table_name} (
    execution_id BIGINT,
    assetInfo STRUCT<
        dataSourceName: STRING,
        dataSourceType: STRING,
        assetUid: STRING,
        assetName: STRING,
        assetType: STRING,
        schemaName: STRING,
        reliabilityScore: DOUBLE
    >,
    execution STRUCT<
        id: BIGINT,
        ruleId: BIGINT,
        ruleName: STRING,
        ruleType: STRING,
        executionMode: STRING,
        executionStatus: STRING,
        resultStatus: STRING,
        startedAt: STRING
    >,
    result STRUCT<
        status: STRING,
        description: STRING,
        successCount: INT,
        failureCount: INT,
        warningCount: INT,
        rows: BIGINT,
        failedRows: BIGINT,
        qualityScore: DOUBLE,
        executionStatus: STRING,
        driftScore: DOUBLE,
        anomalyScore: DOUBLE,
        anomalyCount: INT,
        changesDetected: INT,
        delaySeconds: BIGINT,
        slaMet: BOOLEAN
    >,
    items ARRAY<STRUCT<
        id: BIGINT,
        ruleItemId: BIGINT,
        status: STRING,
        success: BOOLEAN,
        resultPercent: DOUBLE,
        threshold: DOUBLE,
        rowsFailed: BIGINT,
        columnName: STRING,
        dimension: STRING,
        columnMapping: STRUCT<
            leftColumnName: STRING,
            rightColumnName: STRING,
            operation: STRING
        >
    >>,
    meta MAP<STRING, STRING>,
    processed_at TIMESTAMP
)
"""
print(f"Executing: CREATE TABLE {table_name}")
res = w.statement_execution.execute_statement(statement=create_sql, warehouse_id=warehouse_id, wait_timeout="30s")
print(res.status.state.value)

today = datetime.date.today()

insert_statements = []

datasets = [
    "taylor_hanson_build_catalog.main.customer_retention_test",
    "taylor_hanson_build_catalog.main.customer_retention_test_1",
    "taylor_hanson_build_catalog.main.customer_retention_test_2",
    "taylor_hanson_build_catalog.main.customer_retention_test_3",
    "taylor_hanson_build_catalog.main.sales_data"
]

for dataset in datasets:
    for i in range(10): # Last 10 days
        d = today - datetime.timedelta(days=i)
        processed_at = d.strftime("%Y-%m-%d %H:%M:%S")

        # Random scores
        reliability = round(random.uniform(85.0, 99.0), 2)
        if i == 0:
            reliability = 92.0 # Fixed for today for predictability
            
        threshold = 95.0
        
        # Introduce some random failures
        if dataset in [
            "taylor_hanson_build_catalog.main.customer_retention_test",
            "taylor_hanson_build_catalog.main.customer_retention_test_1"
        ]:
            # Perfect scores to ensure 0 failures
            result_percent_1 = 100.0
            result_percent_2 = 100.0
        else:
            result_percent_1 = 100.0 if random.random() > 0.3 else 90.0
            result_percent_2 = 100.0 if random.random() > 0.3 else 85.0

        insert_sql = f"""
        INSERT INTO {table_name} (
            execution_id, assetInfo, items, processed_at
        ) VALUES (
            {random.randint(1000, 9999)},
            named_struct(
                'dataSourceName', 'test_source',
                'dataSourceType', 'test_type',
                'assetUid', '{dataset}',
                'assetName', 'test_asset',
                'assetType', 'table',
                'schemaName', 'test_schema',
                'reliabilityScore', {reliability}
            ),
            array(
                named_struct(
                    'id', cast(1 as bigint),
                    'ruleItemId', cast(1 as bigint),
                    'status', 'success',
                    'success', true,
                    'resultPercent', {result_percent_1},
                    'threshold', {threshold},
                    'rowsFailed', cast(0 as bigint),
                    'columnName', 'col1',
                    'dimension', 'dim1',
                    'columnMapping', named_struct('leftColumnName', 'col1', 'rightColumnName', 'col1', 'operation', '=')
                ),
                named_struct(
                    'id', cast(2 as bigint),
                    'ruleItemId', cast(2 as bigint),
                    'status', 'success',
                    'success', true,
                    'resultPercent', {result_percent_2},
                    'threshold', {threshold + 2.0},
                    'rowsFailed', cast(0 as bigint),
                    'columnName', 'col2',
                    'dimension', 'dim2',
                    'columnMapping', named_struct('leftColumnName', 'col2', 'rightColumnName', 'col2', 'operation', '=')
                )
            ),
            cast('{processed_at}' as timestamp)
        )
        """
        insert_statements.append(insert_sql)

for sql in insert_statements:
    w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse_id, wait_timeout="30s")

print("Data inserted!")
