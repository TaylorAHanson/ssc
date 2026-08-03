import os
import glob
import time
import yaml
import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler
from app.core.config import get_scan_catalogs, settings

logger = logging.getLogger(__name__)

# Lookback window (days) used for data-quality evaluation when a table does not
# carry an explicit ``reliability_window`` tag. We still evaluate DQ so that DQ
# failures surface in the SAME scan as the missing-tag finding, rather than only
# appearing on a later run once the tag is added.
DEFAULT_RELIABILITY_WINDOW_DAYS = 7

# ADOC *_history tables that hold per-rule-item results. Each has a slightly
# different `items` struct schema (nested columnMapping/thresholdLevel types
# differ), so UNION-ing the raw `items` array fails with INCOMPATIBLE_COLUMN_TYPE.
# We explode + project only the scalar fields we need inside each arm so the
# unioned columns are all compatible scalar types.
_ADOC_HISTORY_TABLES = [
    "adoc_dq_history",
    "adoc_freshness_history",
    "adoc_data_drift_history",
    "adoc_profile_anomaly_history",
    "adoc_schema_drift_history",
]
_ADOC_ITEM_PROJECTION = (
    "assetInfo.assetUid AS assetUid, assetInfo.assetName AS assetName, "
    "execution.ruleName AS ruleName, execution.ruleType AS ruleType, processed_at, "
    "item.ruleItemId AS ruleItemId, item.columnName AS columnName, "
    "item.dimension AS dimension, item.resultPercent AS resultPercent, "
    "item.threshold AS threshold, item.rowsFailed AS rowsFailed"
)


class DatasetResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        # Registry of (asset_info, full_name, window_days) collected while walking
        # every dataset, so the (expensive) ADOC data-quality history can be read
        # in a few batched queries AFTER discovery rather than once per table.
        dq_targets: List[tuple] = []
        try:
            from app.db.session import get_db
            from app.db.data_contract import DataContractModel
            
            db = next(get_db())
            contracts = db.query(DataContractModel).filter(DataContractModel.is_active == True).all()
            
            contracted_datasets = {}
            for contract in contracts:
                try:
                    dataset_def = yaml.safe_load(contract.yaml_content)
                    full_name = contract.dataset_id
                    contracted_datasets[full_name] = dataset_def or {}
                    contracted_datasets[full_name]["_invalid_yaml"] = False
                except Exception as e:
                    logger.error(f"Failed to parse Data Contract {contract.dataset_id}: {e}")
                    full_name = contract.dataset_id
                    contracted_datasets[full_name] = {"_invalid_yaml": True}
                    
            for dp_name, dataset_def in contracted_datasets.items():
                try:
                    # Initialize an aggregated resource for the data product
                    resource = {
                        "id": dp_name,
                        "dataset_id": dp_name,
                        "type": "data_product",
                        "invalid_yaml": dataset_def.get("_invalid_yaml", False),
                        "assets": []
                    }
                    
                    servers = dataset_def.get("servers", [])
                    default_catalog = servers[0].get("catalog", "") if servers else ""
                    default_schema = servers[0].get("schema", "") if servers else ""
                    
                    schemas = dataset_def.get("schema", [])
                    
                    # We will loop through all physical tables and aggregate metadata
                    for this_schema in schemas:
                        physical_table = this_schema.get("physicalName")
                        if not physical_table:
                            continue
                            
                        table_catalog = this_schema.get("catalog")
                        table_schema = this_schema.get("schema")
                        
                        if table_catalog and table_schema:
                            catalog = table_catalog
                            schema = table_schema
                            # If physical_table already has dots, don't prepend again
                            if "." in physical_table:
                                full_name = physical_table
                            else:
                                full_name = f"{catalog}.{schema}.{physical_table}"
                        elif "." in physical_table and len(physical_table.split(".")) == 3:
                            full_name = physical_table
                            catalog, schema, table = full_name.split(".")
                        else:
                            catalog = default_catalog
                            schema = default_schema
                            if not catalog or not schema:
                                continue
                            full_name = f"{catalog}.{schema}.{physical_table}"
                        
                        asset_info = {
                            "name": full_name,
                            "type": "table",
                            "tags": {},
                            "failed_rule_count": -1,
                            "failed_rules": [],
                            "catalog_description": None,
                            "schema_description": None,
                            "all_columns_have_descriptions": False,
                            "rbac_defined": False,
                            "rbac_readable": False,
                            "table_exists": True,
                            "missing_column_descriptions": []
                        }
                        
                        # Get tags for this specific table
                        try:
                            uc_tags = self.workspace_client.entity_tag_assignments.list(entity_type='tables', entity_name=full_name)
                            for tag_assign in uc_tags:
                                if tag_assign.tag_key:
                                    asset_info["tags"][tag_assign.tag_key] = tag_assign.tag_value
                        except Exception as e:
                            logger.error(f"Failed to fetch tags for {full_name}: {e}")
                            
                        # Evaluate data quality regardless of whether the
                        # reliability_window tag is set. Gating the DQ fetch on the
                        # tag meant DQ failures stayed hidden until someone added the
                        # tag, so deficiencies dribbled out across multiple scans
                        # instead of all at once. When the tag is absent we fall back
                        # to a default lookback window; the missing tag is still
                        # reported separately by the reliability_window_tag rule.
                        #
                        # The actual ADOC *_history read is DEFERRED: instead of one
                        # query per table (each re-scanning the large history tables),
                        # we register this asset + its lookback window and fetch them
                        # all in a few batched queries after discovery. Until then,
                        # failed_rule_count stays -1 ("not fetched").
                        reliability_window = asset_info["tags"].get("reliability_window")
                        # Extract just the number from values like "7-days"; fall back
                        # to the default window when no reliability_window tag is set.
                        digits = "".join([c for c in str(reliability_window) if c.isdigit()]) if reliability_window else ""
                        window_days = int(digits) if digits else DEFAULT_RELIABILITY_WINDOW_DAYS
                        dq_targets.append((asset_info, full_name, window_days))

                        # Fetch metadata from Unity Catalog
                        try:
                            catalog_info = self.workspace_client.catalogs.get(name=catalog)
                            asset_info["catalog_description"] = catalog_info.comment
                        except Exception:
                            asset_info["catalog_description"] = None
                            
                        try:
                            schema_info = self.workspace_client.schemas.get(full_name=f"{catalog}.{schema}")
                            asset_info["schema_description"] = schema_info.comment
                        except Exception:
                            asset_info["schema_description"] = None
                            
                        try:
                            table_info = self.workspace_client.tables.get(full_name=full_name)
                            if hasattr(table_info, 'table_type') and table_info.table_type:
                                t_type = str(table_info.table_type.value) if hasattr(table_info.table_type, 'value') else str(table_info.table_type)
                                asset_info["type"] = "view" if "VIEW" in t_type.upper() else "table"
                            
                            columns = table_info.columns or []
                            missing_cols = [col.name for col in columns if not col.comment]
                            asset_info["all_columns_have_descriptions"] = len(missing_cols) == 0
                            asset_info["missing_column_descriptions"] = missing_cols
                        except Exception as e:
                            logger.warning(f"Failed to fetch table info for {full_name} from Unity Catalog: {e}")
                            asset_info["all_columns_have_descriptions"] = False
                            asset_info["table_exists"] = False
                            # Fallback convention check
                            if full_name.endswith("_v") or full_name.endswith("_view"):
                                asset_info["type"] = "view"
                            
                        # RBAC verification via the Unity Catalog Grants API
                        # (SHOW GRANTS). Reading grants on a UC object requires MANAGE
                        # on the securable, object ownership, or metastore admin
                        # (workspace admin is NOT sufficient for UC objects). When we
                        # CAN'T read them we must not assume "no access controls" (that
                        # would false-flag every table), so we record rbac_readable=False
                        # and the policy skips the check rather than failing it.
                        try:
                            grants = self.workspace_client.grants.get(securable_type="table", full_name=full_name)
                            asset_info["rbac_defined"] = len(grants.privilege_assignments or []) > 0
                            asset_info["rbac_readable"] = True
                        except Exception as e:
                            logger.warning(
                                "Could not read grants for %s (need MANAGE on the catalog/"
                                "schema, object ownership, or metastore admin to SHOW GRANTS) "
                                "— skipping the RBAC check for this asset: %s",
                                full_name, e,
                            )
                            asset_info["rbac_defined"] = False
                            asset_info["rbac_readable"] = False
                        
                        resource["assets"].append(asset_info)
                        
                    resources.append(resource)
                    
                except Exception as e:
                    logger.error(f"Failed to process dataset {dp_name}: {e}")

            # Now that every asset is collected, read the ADOC data-quality
            # history in a handful of batched queries (one per distinct lookback
            # window) instead of one query per table.
            try:
                self._populate_failed_rules_batched(dq_targets)
            except Exception as e:  # noqa: BLE001 - never fail discovery on the DQ batch
                logger.error(f"Batched data-quality fetch failed: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Failed during dataset discovery: {e}")
            
        finally:
            if 'db' in locals():
                db.close()
                
        return resources

    def _build_failed_rules_query(self, adoc_schema: str, window_days: int, full_names: List[str]) -> str:
        """Build ONE query returning the latest failed rule-item per asset for a
        given lookback window, across all ADOC *_history tables.

        Mirrors the previous per-table query (explode + project scalars, keep the
        most recent occurrence per rule-item, only rows where score < threshold)
        but covers every ``full_name`` at once via an OR of ``assetUid LIKE`` so a
        single warehouse scan serves the whole group.
        """
        arms = "\n    UNION ALL\n    ".join(
            f"SELECT {_ADOC_ITEM_PROJECTION} FROM {adoc_schema}.{t} "
            "LATERAL VIEW explode(items) exploded AS item"
            for t in _ADOC_HISTORY_TABLES
        )
        # Match any requested asset (assetUid contains the full dotted name), same
        # semantics as the old per-asset LIKE '%full_name%'. Single-quotes in a UC
        # name are not valid, so no escaping is required.
        likes = " OR ".join(f"assetUid LIKE '%{fn}%'" for fn in full_names)
        asset_filter = f"      AND ({likes})\n" if likes else ""
        return f"""
WITH exploded AS (
    {arms}
),
ranked AS (
    SELECT assetUid, assetName, ruleName, ruleType, columnName, dimension,
           resultPercent, threshold, rowsFailed, processed_at,
           ROW_NUMBER() OVER (PARTITION BY assetUid, ruleItemId ORDER BY processed_at DESC) AS rn
    FROM exploded
    WHERE cast(processed_at AS date) >= date_sub(current_date(), {window_days})
      AND resultPercent < threshold
{asset_filter}
)
SELECT assetUid, assetName, ruleName, ruleType, columnName, dimension, resultPercent, threshold, rowsFailed
FROM ranked
WHERE rn = 1
ORDER BY resultPercent ASC
"""

    def _run_dq_statement(self, query: str) -> tuple:
        """Execute a DQ statement, poll to completion, and collect ALL chunks.

        Returns ``(state, rows)``. Two correctness guarantees the naive
        "execute + read data_array" pattern misses — and that batching makes
        important:

        * **Only SUCCEEDED yields rows.** ``execute_statement`` only blocks up to
          its ``wait_timeout`` (max 50s); a heavy batched scan can return still
          RUNNING. We poll ``get_statement`` to a terminal state instead of
          reading an empty first chunk and mistaking an unfinished query for a
          clean (zero-failure) result. Any non-SUCCEEDED state returns ``[]`` with
          that state so the caller leaves the affected assets "not fetched" (-1).
        * **All chunks, not just the first.** A large batched result spans
          multiple chunks; we follow ``next_chunk_index`` so failures aren't
          silently truncated (which would under-report / falsely clear assets).
        """
        resp = self.workspace_client.statement_execution.execute_statement(
            statement=query,
            warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
            wait_timeout="50s",
        )
        statement_id = resp.statement_id

        deadline = time.monotonic() + 300  # overall poll budget
        while True:
            state = resp.status.state.value if (resp.status and resp.status.state) else "UNKNOWN"
            if state in ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED"):
                break
            if time.monotonic() >= deadline:
                logger.error("Batched DQ query still %s after poll budget; treating as not fetched.", state)
                break
            time.sleep(2)
            resp = self.workspace_client.statement_execution.get_statement(statement_id)

        state = resp.status.state.value if (resp.status and resp.status.state) else "UNKNOWN"
        if state != "SUCCEEDED":
            return state, []

        rows: List[Any] = []
        result = resp.result
        while result is not None:
            if result.data_array:
                rows.extend(result.data_array)
            next_idx = getattr(result, "next_chunk_index", None)
            if next_idx is None or next_idx < 0:
                break
            result = self.workspace_client.statement_execution.get_statement_result_chunk_n(
                statement_id, next_idx
            )
        return state, rows

    def _populate_failed_rules_batched(self, dq_targets: List[tuple]) -> None:
        """Fill in each asset's failed DQ rules with one query per distinct window.

        The ADOC *_history tables are large; the previous approach re-scanned them
        once per table (N warehouse round-trips, each waiting up to 30s). Assets
        that share a reliability window can be served by a single scan, so this
        cuts the DQ cost from O(tables) to O(distinct windows) — usually 1–3
        queries for an entire run. Assets in a group whose query fails keep
        failed_rule_count = -1 ("not fetched"); a successful query that returns no
        rows for an asset yields 0 (fetched, clean).
        """
        if not dq_targets:
            return
        if not (hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID):
            logger.warning("No warehouse configured — skipping data-quality fetch for %d asset(s).", len(dq_targets))
            return

        # No hardcoded default: guessing a catalog here silently pointed every
        # environment at the same (stage) DQ history. Unset means "can't fetch",
        # which leaves failed_rule_count = -1 and fails the dq_history_fetched
        # policy rule — certification stays closed rather than passing on data
        # from the wrong environment.
        adoc_schema = (settings.DATA_QUALITY_ADOC_SCHEMA or "").strip()
        if not adoc_schema:
            logger.warning(
                "DATA_QUALITY_ADOC_SCHEMA is not set — skipping the data-quality fetch "
                "for %d asset(s). They stay 'not fetched', so certification will not "
                "pass until Admin -> Settings -> ADOC history schema names the "
                "catalog.schema holding this environment's ADOC *_history tables.",
                len(dq_targets),
            )
            return

        # Group assets by lookback window so each query applies the right filter.
        by_window: Dict[int, List[tuple]] = {}
        for asset_info, full_name, window_days in dq_targets:
            by_window.setdefault(window_days, []).append((asset_info, full_name))

        logger.info(
            "Batched data-quality fetch: %d asset(s) across %d distinct window(s) %s.",
            len(dq_targets), len(by_window), sorted(by_window.keys()),
        )

        for window_days, group in by_window.items():
            full_names = [fn for _, fn in group]
            query = self._build_failed_rules_query(adoc_schema, window_days, full_names)
            try:
                state, rows = self._run_dq_statement(query)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Batched DQ query raised for window=%dd (%d asset(s)): %s",
                    window_days, len(full_names), e,
                )
                continue  # leave failed_rule_count = -1 for this group

            if state != "SUCCEEDED":
                logger.error(
                    "Batched DQ query ended in state=%s for window=%dd (%d asset(s)); "
                    "leaving those assets as not-fetched.",
                    state, window_days, len(full_names),
                )
                continue  # leave failed_rule_count = -1 for this group

            # The query succeeded → every asset in this group is now "fetched".
            for asset_info, _ in group:
                asset_info["failed_rules"] = []
                asset_info["failed_rule_count"] = 0

            for r in rows:
                # Pad short rows so unpacking is safe; column order matches the
                # SELECT list. The statement API returns all values as strings.
                r = list(r) + [None] * (9 - len(r))
                asset_uid, asset_name, rule_name, rule_type, column_name, dimension, result_percent, threshold, rows_failed = r[:9]
                uid = asset_uid or ""
                for asset_info, full_name in group:
                    # Attribute the row exactly like the old per-asset query did:
                    # assetUid LIKE '%full_name%'. (No assetName fallback — the SQL
                    # already filters on assetUid, and matching assetName too could
                    # misattribute a row to a second asset.)
                    if full_name in uid:
                        asset_info["failed_rules"].append({
                            "rule": rule_name or "Unnamed rule",
                            "rule_type": rule_type,
                            "table": asset_name or full_name,
                            "column": column_name,
                            "dimension": dimension,
                            "score": float(result_percent) if result_percent not in (None, "") else None,
                            "threshold": float(threshold) if threshold not in (None, "") else None,
                            "rows_failed": int(rows_failed) if rows_failed not in (None, "") else None,
                        })

            for asset_info, _ in group:
                asset_info["failed_rule_count"] = len(asset_info["failed_rules"])

    def _resolve_physical_tables(self, resource_id: str) -> List[str]:
        """Resolve the fully-qualified physical tables backing a data product.

        Reads the active ODCS contract for ``resource_id`` and expands its
        ``schema`` entries to ``catalog.schema.table`` names, applying the same
        catalog/schema fallbacks used during discovery. Returns ``[]`` when
        there is no active contract or it can't be parsed. Centralizing this
        here keeps certify / uncertify / status-read in lockstep on exactly
        which tables make up a product.
        """
        from app.db.session import get_db
        from app.db.data_contract import DataContractModel

        db = next(get_db())
        try:
            contract = db.query(DataContractModel).filter(
                DataContractModel.dataset_id == resource_id,
                DataContractModel.is_active == True
            ).first()
        finally:
            db.close()

        if not contract:
            logger.error(f"No active contract found for data product {resource_id}")
            return []

        try:
            dataset_def = yaml.safe_load(contract.yaml_content) or {}
        except Exception as e:
            logger.error(f"Failed to parse contract YAML for {resource_id}: {e}")
            return []

        servers = dataset_def.get("servers", [])
        default_catalog = servers[0].get("catalog", "") if servers else ""
        default_schema = servers[0].get("schema", "") if servers else ""

        tables: List[str] = []
        for this_schema in dataset_def.get("schema", []) or []:
            physical_table = this_schema.get("physicalName")
            if not physical_table:
                continue

            table_catalog = this_schema.get("catalog")
            table_schema = this_schema.get("schema")

            if table_catalog and table_schema:
                if "." in physical_table:
                    full_name = physical_table
                else:
                    full_name = f"{table_catalog}.{table_schema}.{physical_table}"
            elif "." in physical_table and len(physical_table.split(".")) == 3:
                full_name = physical_table
            else:
                if not default_catalog or not default_schema:
                    continue
                full_name = f"{default_catalog}.{default_schema}.{physical_table}"

            tables.append(full_name)

        return tables

    @staticmethod
    def _catalog_in_scope(full_name: str) -> bool:
        """Whether a fully-qualified object sits inside the governed catalogs.

        Unity Catalog is metastore-global, so every deployment that shares a
        metastore can *reach* every catalog — the only thing separating a stage
        deployment from prod tables is UC grants. This re-checks the
        ``SCAN_CATALOGS`` allowlist at the moment of the write so a contract that
        somehow references an out-of-scope catalog (a stale row, a restored
        backup, an allowlist tightened after discovery) can't be tagged.

        A blank allowlist means "no scope configured" and permits everything,
        preserving existing behaviour — but it is logged as the risk it is.
        """
        allowlist = get_scan_catalogs()
        if not allowlist:
            logger.warning(
                "Certification is running WITHOUT a catalog allowlist: SCAN_CATALOGS "
                "is blank, so any catalog this service principal can write to may be "
                "tagged (including catalogs belonging to another environment). Set "
                "Admin -> Settings -> Scanned catalogs to scope it."
            )
            return True
        catalog = full_name.split(".")[0].strip("`")
        return catalog in allowlist

    def _apply_certification_tag(self, full_name: str, certified: bool) -> bool:
        """Set/unset ``system.certification_status`` on ONE object, handling both
        tables and views.

        Databricks requires ``ALTER VIEW`` (not ``ALTER TABLE``) to tag a view;
        an ``ALTER TABLE`` against a view fails, which is why views were silently
        left un-tagged. We try ``ALTER TABLE`` first and, on failure, fall back to
        ``ALTER VIEW`` so every object in the product gets tagged regardless of
        type. Returns True only when a statement actually succeeds.

        This is the single chokepoint for every certification tag write, so the
        catalog-scope check lives here rather than in ``certify``/``uncertify``.
        """
        if not self._catalog_in_scope(full_name):
            logger.error(
                "Refusing to %s %s: its catalog is not in SCAN_CATALOGS (%s). A data "
                "contract is referencing a catalog outside this deployment's governed "
                "scope — investigate before widening the allowlist.",
                "certify" if certified else "uncertify", full_name, get_scan_catalogs(),
            )
            return False

        if certified:
            table_sql = f"ALTER TABLE {full_name} SET TAGS ('system.certification_status' = 'certified')"
            view_sql = f"ALTER VIEW {full_name} SET TAGS ('system.certification_status' = 'certified')"
        else:
            table_sql = f"ALTER TABLE {full_name} UNSET TAGS ('system.certification_status')"
            view_sql = f"ALTER VIEW {full_name} UNSET TAGS ('system.certification_status')"

        def _run(sql: str):
            res = self.workspace_client.statement_execution.execute_statement(
                statement=sql,
                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                wait_timeout="30s",
            )
            state = res.status.state.value if (res.status and res.status.state) else "UNKNOWN"
            err = res.status.error.message if (res.status and res.status.error) else None
            return state, err

        state, err = _run(table_sql)
        if state not in ("FAILED", "CANCELED", "CLOSED"):
            return True

        # ALTER TABLE failed — most commonly because the object is a VIEW. Retry
        # as a view before giving up.
        logger.info(
            "Certification tag via ALTER TABLE failed for %s (%s); retrying as ALTER VIEW.",
            full_name, err,
        )
        v_state, v_err = _run(view_sql)
        if v_state not in ("FAILED", "CANCELED", "CLOSED"):
            return True

        logger.error(
            "Failed to %s %s as both table and view (table_err=%s | view_err=%s)",
            "certify" if certified else "uncertify", full_name, err, v_err,
        )
        return False

    async def certify(self, resource_id: str) -> bool:
        logger.info(f"Certifying data product {resource_id}")
        try:
            if not hasattr(settings, "DATABRICKS_WAREHOUSE_ID") or not settings.DATABRICKS_WAREHOUSE_ID:
                logger.error("No warehouse_id defined, cannot certify dataset via SQL")
                return False

            tables = self._resolve_physical_tables(resource_id)
            if not tables:
                logger.warning(f"Certify {resource_id}: contract resolved 0 physical tables — nothing tagged.")
                return False

            failed = [t for t in tables if not self._apply_certification_tag(t, certified=True)]
            logger.info(
                "Certify %s: tagged %d/%d object(s) as certified%s.",
                resource_id, len(tables) - len(failed), len(tables),
                f" (failed: {failed})" if failed else "",
            )
            return not failed
        except Exception as e:
            logger.error(f"Failed to certify dataset {resource_id}: {e}")
            return False

    async def uncertify(self, resource_id: str) -> bool:
        logger.info(f"Un-certifying data product {resource_id}")
        try:
            if not hasattr(settings, "DATABRICKS_WAREHOUSE_ID") or not settings.DATABRICKS_WAREHOUSE_ID:
                logger.error("No warehouse_id defined, cannot uncertify dataset via SQL")
                return False

            tables = self._resolve_physical_tables(resource_id)
            if not tables:
                logger.warning(f"Uncertify {resource_id}: contract resolved 0 physical tables — nothing changed.")
                return False

            failed = [t for t in tables if not self._apply_certification_tag(t, certified=False)]
            logger.info(
                "Uncertify %s: cleared tag on %d/%d object(s)%s.",
                resource_id, len(tables) - len(failed), len(tables),
                f" (failed: {failed})" if failed else "",
            )
            return not failed
        except Exception as e:
            logger.error(f"Failed to un-certify dataset {resource_id}: {e}")
            return False

    async def get_certification_status(self, resource_id: str) -> bool:
        """Read the live Unity Catalog certification state for a data product.

        This is the source-of-truth rollup the certification UI relies on: a
        product is certified iff it has at least one backing table and *every*
        table carries ``system.certification_status = certified``. Reading the
        UC tag back (rather than assuming the action succeeded) means the
        cached flag self-heals against partial failures and out-of-band tag
        changes. If any tag read fails we conservatively report uncertified so
        we never show a stale "certified" badge.
        """
        tables = self._resolve_physical_tables(resource_id)
        if not tables:
            return False

        for full_name in tables:
            tag_value = None
            try:
                assignments = self.workspace_client.entity_tag_assignments.list(
                    entity_type='tables', entity_name=full_name
                )
                for assignment in assignments:
                    if assignment.tag_key == 'system.certification_status':
                        tag_value = assignment.tag_value
                        break
            except Exception as e:
                logger.warning(f"Failed to read certification tag for {full_name}: {e}")
                return False

            if str(tag_value or "").lower() != "certified":
                return False

        return True

    async def kill(self, resource_id: str) -> bool:
        return await self.uncertify(resource_id)

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of dataset {resource_id}: {message}")
        return True
