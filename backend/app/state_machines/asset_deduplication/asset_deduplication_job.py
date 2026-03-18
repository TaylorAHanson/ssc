# Databricks notebook source
# Asset Deduplication Job
# Automated Governance Pipeline for detecting near-duplicate assets in Unity Catalog.

import pyspark.sql.functions as F
from pyspark.sql.types import StructType, StructField, StringType, LongType, BooleanType
import datetime, re
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

# --- Parameters ---
dbutils.widgets.text("target_catalog", "")
dbutils.widgets.text("reference_catalog", "")
dbutils.widgets.text("run_id", "manual")
dbutils.widgets.text("results_table", "")
dbutils.widgets.text("blocker_threshold", "0.90")
dbutils.widgets.text("warn_threshold", "0.75")

target_catalog = dbutils.widgets.get("target_catalog")
reference_catalog = dbutils.widgets.get("reference_catalog")
run_id = dbutils.widgets.get("run_id")
results_table = dbutils.widgets.get("results_table")
BLOCKER_THRESHOLD = float(dbutils.widgets.get("blocker_threshold"))
WARN_THRESHOLD = float(dbutils.widgets.get("warn_threshold"))

if not target_catalog or not reference_catalog or not results_table:
    raise ValueError("target_catalog, reference_catalog, and results_table are required")

print(f"Running Deduplication Task: Target={target_catalog}, Reference={reference_catalog}, RunID={run_id}")

# --- 1. Ingest Metadata ---
print("Step 1: Ingesting Metadata...")

def get_detailed_metadata(catalog):
    """Collects tables, columns, comments, volume, and lineage from the catalog."""
    # Table-level metadata
    tables_df = spark.sql(f"""
        SELECT 
            table_catalog as catalog,
            table_schema as schema,
            table_name,
            table_type,
            comment as table_comment
        FROM system.information_schema.tables 
        WHERE table_catalog = '{catalog}'
          AND table_schema != 'information_schema'
    """)
    
    # Column-level metadata (Aggregated)
    columns_df = spark.sql(f"""
        SELECT 
            table_catalog as catalog,
            table_schema as schema,
            table_name,
            collect_list(struct(
                column_name,
                data_type,
                comment as col_comment
            )) as columns
        FROM system.information_schema.columns
        WHERE table_catalog = '{catalog}'
        GROUP BY 1, 2, 3
    """)
    
    base_df = tables_df.join(columns_df, ["catalog", "schema", "table_name"], "left")

    # --- ENHANCEMENT: Volume/Shape (DESCRIBE DETAIL) ---
    print(f"  Collecting volume signals for {catalog}...")
    
    details_schema = StructType([
        StructField("schema", StringType(), True),
        StructField("table_name", StringType(), True),
        StructField("size_in_bytes", LongType(), True),
        StructField("num_files", LongType(), True)
    ])
    
    table_rows = base_df.select("schema", "table_name").collect()
    
    def get_table_details(r):
        schema_name = r["schema"]
        table_name = r["table_name"]
        full_name = f"`{catalog}`.`{schema_name}`.`{table_name}`"
        
        size_in_bytes = 0
        num_files = 0
        
        # Get Size and Files
        try:
            detail_row = spark.sql(f"SELECT sizeInBytes, numFiles FROM (DESCRIBE DETAIL {full_name})").collect()
            if detail_row:
                size_in_bytes = detail_row[0][0] or 0
                num_files = detail_row[0][1] or 0
        except Exception:
            pass # Ignore views or inaccessible tables
            
        return (schema_name, table_name, size_in_bytes, num_files)
    
    details_list = []
    # Use max_workers based on number of tables, capped at 20 to avoid overwhelming the driver
    workers = min(len(table_rows) if table_rows else 1, 20)
    
    if table_rows:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            details_list = list(executor.map(get_table_details, table_rows))
        
    if details_list:
        details_df = spark.createDataFrame(details_list, schema=details_schema)
    else:
        details_df = spark.createDataFrame([], schema=details_schema)
        
    base_df = base_df.join(details_df, ["schema", "table_name"], "left")

    # --- ENHANCEMENT: History (CLONE Detection via system.access.audit) ---
    print(f"  Collecting history signals from system.access.audit for {catalog}...")
    
    base_df = base_df.withColumn("full_name_lower", F.lower(F.concat_ws(".", "catalog", "schema", "table_name")))
    
    try:
        # Derive has_clone per table from audit logs (distributed scan instead of per-table DESCRIBE HISTORY)
        clone_events = spark.sql(f"""
          SELECT
            lower(request_params.table_full_name) AS full_name_lower
          FROM system.access.audit
          WHERE service_name = 'unityCatalog'
            AND action_name IN ('runCommand', 'commandSubmit')
            AND lower(request_params.commandText) LIKE '% clone %'
        """).distinct()
        
        clone_df = clone_events.withColumn("has_clone", F.lit(True))
        
        # Join back to all tables, default False if no clone event
        base_df = base_df.join(clone_df, "full_name_lower", "left") \
                         .withColumn("has_clone", F.coalesce(F.col("has_clone"), F.lit(False)))
    except Exception as e:
        print(f"Warning: Audit log table not accessible for clone detection: {e}")
        base_df = base_df.withColumn("has_clone", F.lit(False))

    # Clean up the temporary lowercase full_name column
    base_df = base_df.drop("full_name_lower")

    # --- ENHANCEMENT: Lineage (Lineage System Tables) ---
    try:
        lineage_df = spark.sql(f"""
            SELECT 
                target_table_full_name as full_name,
                collect_set(lower(source_table_full_name)) as upstreams
            FROM system.access.table_lineage
            WHERE target_table_catalog = '{catalog}'
            GROUP BY 1
        """)
    except Exception as e:
        print(f"Warning: Lineage table not accessible for {catalog}: {e}")
        # Create empty lineage dataframe
        lineage_df = spark.sql("SELECT cast('' as string) as full_name, array() as upstreams WHERE 1=0")
    
    return base_df.withColumn("full_name", F.concat_ws(".", "catalog", "schema", "table_name")) \
                  .join(lineage_df, "full_name", "left") \
                  .withColumn("upstreams", F.coalesce(F.col("upstreams"), F.array()))

target_metadata = get_detailed_metadata(target_catalog)
ref_metadata = get_detailed_metadata(reference_catalog)

# --- 2. Compute Features ---
print("Step 2: Computing Features...")

def build_features(df):
    """Normalizes tokens and builds signatures/feature sets."""
    # 1. Schema Signature & Count
    df = df.withColumn("col_names_norm", F.expr("""
        transform(columns, c -> lower(regexp_replace(c.column_name, '[^a-zA-Z0-9_]', '')))
    """))
    df = df.withColumn("num_columns", F.size("columns"))
    
    # 2. Lineage Feature: Sorted upstream names for Jaccard
    df = df.withColumn("upstreams_norm", F.array_sort("upstreams"))
    
    # 3. Tokenize comments for native Jaccard (handle nulls gracefully)
    df = df.withColumn("comment_tokens", F.coalesce(
        F.expr("split(lower(regexp_replace(coalesce(table_comment, ''), '[^a-zA-Z0-9\\\\s]', '')), '\\\\s+')"),
        F.array()
    ))

    return df

target_features = build_features(target_metadata)
ref_features = build_features(ref_metadata)

# --- 3. Generate Candidates ---
print("Step 3: Generating Candidates...")

# Precompute a schema minhash/hash to tighten blocking
# This significantly reduces candidates when many tables have the same number of columns
target_features = target_features.withColumn("schema_hash", F.hash(F.array_join("col_names_norm", "|")))
ref_features = ref_features.withColumn("schema_hash", F.hash(F.array_join("col_names_norm", "|")))

# Candidate generation by blocking on num_columns AND schema_hash
# We also exclude self-matches if target and reference are the same catalog.
candidates = target_features.alias("t").join(
    ref_features.alias("r"),
    (F.col("t.num_columns") == F.col("r.num_columns")) & 
    (F.col("t.schema_hash") == F.col("r.schema_hash")),
    "inner"
).where(F.col("t.full_name") != F.col("r.full_name")) \
.select(
    F.col("t.full_name").alias("target_full_name"),
    F.col("r.full_name").alias("reference_full_name"),
    F.col("t.col_names_norm").alias("t_cols"),
    F.col("r.col_names_norm").alias("r_cols"),
    F.col("t.comment_tokens").alias("t_comment_tokens"),
    F.col("r.comment_tokens").alias("r_comment_tokens"),
    F.col("t.upstreams_norm").alias("t_upstreams"),
    F.col("r.upstreams_norm").alias("r_upstreams"),
    F.col("t.size_in_bytes").alias("t_size"),
    F.col("r.size_in_bytes").alias("r_size"),
    F.col("t.has_clone").alias("t_clone"),
    F.col("r.has_clone").alias("r_clone")
)

# --- 4. Score Pairs ---
print("Step 4: Scoring Pairs...")

def compute_jaccard(col_a, col_b):
    """Computes Jaccard similarity using Spark native array functions."""
    intersection_size = F.size(F.array_intersect(col_a, col_b))
    union_size = F.size(F.array_union(col_a, col_b))
    return F.when(union_size == 0, 0.0).otherwise(intersection_size / union_size)

# Compute Schema Similarity (Coalesce to 0.0 to prevent NULL propagation)
scored_df = candidates.withColumn("s_schema", F.coalesce(compute_jaccard("t_cols", "r_cols"), F.lit(0.0)))

# Compute Description Similarity (Coalesce to 0.0 to prevent NULL propagation)
scored_df = scored_df.withColumn("s_desc", F.coalesce(compute_jaccard("t_comment_tokens", "r_comment_tokens"), F.lit(0.0)))

# Compute Lineage Similarity (Coalesce to 0.0 to prevent NULL propagation)
scored_df = scored_df.withColumn("s_lineage", F.coalesce(compute_jaccard("t_upstreams", "r_upstreams"), F.lit(0.0)))

# --- ENHANCEMENT: Volume Similarity (Ratio-based) ---
scored_df = scored_df.withColumn("s_volume", F.expr("""
    case 
        when t_size = 0 and r_size = 0 then 1.0
        when t_size = 0 or r_size = 0 then 0.0
        else least(t_size, r_size) / greatest(t_size, r_size) 
    end
"""))

# --- ENHANCEMENT: Delta Similarity (Clone Detection) ---
scored_df = scored_df.withColumn("s_delta", F.when(F.col("t_clone") == F.col("r_clone"), 1.0).otherwise(0.0))

# Composite Score based on Design Doc Weights:
# w_schema=0.35, w_desc=0.20, w_lineage=0.25, w_volume=0.10, w_delta=0.10
scored_df = scored_df.withColumn("similarity", (
    (F.col("s_schema") * 0.35) + 
    (F.col("s_desc") * 0.20) + 
    (F.col("s_lineage") * 0.25) + 
    (F.col("s_volume") * 0.10) + 
    (F.col("s_delta") * 0.10)
))

# --- 5. Classify & Persist ---
print("Step 5: Classifying and Persisting Results...")

final_matches_df = scored_df.withColumn("policy_class", F.expr(f"""
    CASE 
        WHEN similarity >= {BLOCKER_THRESHOLD} THEN 'BLOCKER'
        WHEN similarity >= {WARN_THRESHOLD} THEN 'WARN'
        ELSE 'INFO'
    END
"""))

final_matches_df = final_matches_df.withColumn("explanation", F.expr("""
    concat(
        'Score breakdown: ',
        'Schema (', round(coalesce(s_schema, 0.0) * 100, 1), '%), ',
        'Desc (', round(coalesce(s_desc, 0.0) * 100, 1), '%), ',
        'Lineage (', round(coalesce(s_lineage, 0.0) * 100, 1), '%), ',
        'Vol (', round(coalesce(s_volume, 0.0) * 100, 1), '%), ',
        'Delta (', round(coalesce(s_delta, 0.0) * 100, 1), '%).'
    )
"""))

# Add run metadata
final_matches_df = final_matches_df.select(
    "target_full_name",
    "reference_full_name",
    F.col("s_schema").cast("double"),
    F.col("s_desc").cast("double"),
    F.col("s_lineage").cast("double"),
    F.col("s_volume").cast("double"),
    F.col("s_delta").cast("double"),
    F.col("similarity").cast("double"),
    "policy_class",
    "explanation",
    F.lit(run_id).alias("run_id"),
    F.current_timestamp().alias("scored_at")
)

# Create the governance schema if it doesn't exist
# We assume the user provides a full table path: catalog.schema.table
parts = results_table.split(".")
if len(parts) >= 2:
    schema_path = ".".join(parts[:-1])
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_path}")

# Simplified results for demonstration: write as Delta table
final_matches_df.write.format("delta").mode("append").option("overwriteSchema", "true").saveAsTable(results_table)

print(f"Asset Deduplication Job Completed Successfully. Results written to {results_table}.")
