# Metric View Lineage: REST API vs System Tables

Why `_fetch_downstream_dashboards` (`backend/app/workers/tasks/sync_data_assets.py`)
times out, and the supported way to find the dashboards that read a metric view.
Researched 2026-09-24 against the `fevm-taylor-hanson-build` workspace. Times are UTC.

## Summary

1. **Does the lineage REST API support metric views?** It accepts metric-view names
   (no 400/404), but every call we made — metric view or table — was rate-limited, so we
   never saw a successful response. The endpoint `/api/2.0/lineage-tracking/table-lineage`
   is not in the public REST API reference; it is the API the Catalog Explorer UI uses.
2. **Why do the calls hang?** Not metric-view specific. The endpoint returns
   `429 RESOURCE_EXHAUSTED` ("Rate limit exceeded") with no `Retry-After` header, so the
   SDK retries every second until its 120 s retry deadline. The sync runs 8 lookups at once,
   so each sync sends ~8 requests/s at the endpoint for as long as it runs — which keeps the
   limit exhausted. Every running copy of the app (the dev app and any local dev server)
   adds to this.
3. **Supported way to find dashboards reading a metric view:** the documented system table
   `system.access.table_lineage`, filtering `source_type = 'METRIC_VIEW'` and
   `entity_type = 'DASHBOARD_V3'`. It holds a rolling one year of data and answered our
   queries in under 2 s.

**Recommendation:** replace the REST calls with one `system.access.table_lineage` query per
sync (see the sketch at the end).

**Status (2026-09-24):** implemented in `_fetch_downstream_dashboards`; the lookback window is
`DATA_ASSET_LINEAGE_LOOKBACK_DAYS` (Admin -> Settings, default 90). Two other callers still use
the REST endpoint for table-to-table lineage and will hit the same 429s: the Discover Lineage
tab (`GET /data-assets/databricks/lineage`, `backend/app/api/v1/data_assets.py`) and ODCS
contract drafting (`fetch_datasets_metadata`, `backend/app/tools/governance/draft_odcs.py`).

---

## 1. Lineage REST API and metric views

- **Not a public API.** The endpoint does not appear in the Databricks REST API reference
  (https://docs.databricks.com/api/). It is the endpoint Catalog Explorer uses, so it has no
  documented contract, rate limit, or stability guarantee.
- **Metric views are accepted.** Requests for metric views fail the same way as requests for
  tables (429, below), not with 400/404, so the endpoint does not reject them. We could not
  confirm what a successful response for a metric view contains, because no request got
  through the rate limit.

## 2. Why the calls hang

### What the endpoint returned

| Object | Type | Result | When |
|---|---|---|---|
| `gtm_analytics.gold.gtm_retention_metrics` | metric view | 429, retried until deadline | 18:10 and 18:14 |
| `taylor_hanson_build_catalog.main.customer_retention_test_3` | table | 429, retried until deadline | 18:10 and 18:14 |

Response body (observed 18:14 with SDK debug logging, `retry_timeout_seconds=1`):

```
< 429 Too Many Requests
<   "error_code": "RESOURCE_EXHAUSTED",
<   "message": "Rate limit exceeded. Please try again later."
DEBUG:root:No Retry-After header received in response with status code 429 or 503. Defaulting to 1
```

### How the SDK retries (databricks-sdk, installed in `backend/venv`)

- **429 and 503 are retried.** They map to `TooManyRequests` / `TemporarilyUnavailable`
  (`databricks/sdk/errors/platform.py:26, 42`). The retry loop uses the error's
  `retry_after_secs` when the server sends `Retry-After`, otherwise it sleeps 1 s
  (`databricks/sdk/retries.py:45-48`, and the debug line above).
- **Connection errors and timeouts are retried** (`databricks/sdk/_base_client.py`, the
  `_is_retryable` checks around lines 247-255). 400/401/403/404 are not.
- **`retry_timeout_seconds`** is the total deadline for all attempts of one call;
  **`http_timeout_seconds`** limits a single attempt. The app sets 120 s and 60 s
  (`backend/app/providers/databricks/client.py`, `_build_workspace_client`).

### Why this never recovers

`_fetch_downstream_dashboards` runs up to 8 lookups at once (`asyncio.Semaphore(8)`), each on
a worker thread. Against a rate-limited endpoint each lookup sends one request per second
until its 120 s deadline, so a sync sends ~8 requests/s at the endpoint throughout. During
this investigation both the dev app (hourly) and a local dev server were syncing against the
same workspace — the local server logged lineage timeouts every minute from 12:07 to 12:14
local time — so the limit had no chance to reset.

Side effect: the 8 blocked threads take up the poller's default thread pool, so other
background work waits. On dev at 18:00, a Sentinel run finished scanning at 18:01:04 but was
not marked complete until 18:02:43, the moment the lineage timeouts released their threads.

## 3. Supported path: `system.access.table_lineage`

From the lineage system tables reference
(https://docs.databricks.com/aws/en/admin/system-tables/lineage):

- `entity_type` is `NOTEBOOK`, `JOB`, `PIPELINE`, `DASHBOARD_V3`, `DBSQL_DASHBOARD`
  (deprecated), `DBSQL_QUERY`, or NULL.
- `source_type` / `target_type` is `TABLE`, `PATH`, `VIEW`, `MATERIALIZED_VIEW`,
  `METRIC_VIEW`, or `STREAMING_TABLE`.
- "Lineage system tables retain a rolling 1-year window of data."
- No latency or freshness guarantee is stated.

From the system tables overview (https://docs.databricks.com/aws/en/admin/system-tables/):

- `table_lineage` and `column_lineage` have 365 days of free retention and are **regional**
  (they cover workspaces in the same region).
- System tables are governed by Unity Catalog. Non-admins need `USE CATALOG` on `system`,
  `USE SCHEMA` on the schema, and `SELECT`. Account admins who are also metastore admins
  have access by default. **The identity the sync uses (the governance service principal)
  needs these grants.**

Observed on this workspace (SELECT-only queries, ~18:10):

| Query (last 24 h unless noted) | Result |
|---|---|
| rows with `source_type = 'METRIC_VIEW'`, last 7 days | 33,946 |
| rows with `entity_type = 'DASHBOARD_V3'` | 105,710 |
| rows with `source_type = 'METRIC_VIEW' AND entity_type = 'DASHBOARD_V3'` | 206 |
| query time | under 2 s on a serverless warehouse |

For dashboard rows, `entity_id` held the dashboard id (e.g.
`calsaws_aibi_demo_v2_catalog.gold.pending_applications_metrics` →
`01f1b76a6cb013d5b121d5252df91d13`). One read produces several rows, so group by
(`source_table_full_name`, `entity_id`).

## Recommendation for `_fetch_downstream_dashboards`

Replace the per-view REST calls with one query per sync:

- **Reliable:** documented table, no per-view calls, no rate limit, no 2-minute hangs, no
  blocked threads.
- **Cheaper:** one SQL statement instead of one HTTP call per metric view.
- **Trade-offs:** needs the three system-table grants for the sync's identity; shows only
  dashboards that actually read the view within the chosen window (e.g. 90 days of the
  year kept); lineage is regional; freshness is not documented.

Sketch (quotes names with `quote_literal`, like the other queries fixed in this change):

```python
from app.tools.sql_safety import quote_literal

async def _fetch_downstream_dashboards(provider, fqns: list[str]) -> dict[str, list[dict]]:
    if not fqns:
        return {}
    names = ", ".join(quote_literal(f) for f in fqns)
    result = await provider.execute_sql(f"""
        SELECT source_table_full_name, entity_id, MAX(event_time) AS last_read
        FROM system.access.table_lineage
        WHERE event_date >= current_date() - INTERVAL 90 DAYS
          AND source_type = 'METRIC_VIEW'
          AND entity_type = 'DASHBOARD_V3'
          AND source_table_full_name IN ({names})
        GROUP BY source_table_full_name, entity_id
    """)
    # ...then resolve dashboard names/links from the home workspace's Lakeview
    # dashboards exactly as the current code does, keyed by entity_id.
```

Until that lands, a short per-call timeout and a smaller concurrency limit on the REST calls
would stop the hangs, but would not make the lookups succeed while the endpoint is
rate-limited.

## Open questions

1. What the endpoint's rate limit is (per user, per workspace?) and how long it takes to reset
   once nothing is calling it. Not measured: the dev app and local server were syncing
   throughout.
2. What a successful REST response looks like for a metric view (none got through).
3. How fresh `system.access.table_lineage` is for dashboard reads (not documented).
4. Whether the governance service principal already has the system-table grants on each
   deployment.
