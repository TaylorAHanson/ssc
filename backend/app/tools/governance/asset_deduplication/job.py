# Setup & Configuration
import pyspark.sql.functions as F
from pyspark.sql.types import IntegerType, ArrayType

# --- Widgets ---
dbutils.widgets.text("target_catalog", "dbdemos", "1. Target Catalog")
dbutils.widgets.text("target_schema", "", "2. Target Schema (Optional)") 
dbutils.widgets.text("reference_catalog", "system", "3. Reference Catalog")
dbutils.widgets.text("assets_table", "governance.uc_similarity_assets", "4. Output Assets Table")
dbutils.widgets.text("matches_table", "governance.uc_similarity_matches", "5. Output Matches Table")

# --- Parameters ---
target_cat = dbutils.widgets.get("target_catalog")
target_sch = dbutils.widgets.get("target_schema").strip()
ref_cat = dbutils.widgets.get("reference_catalog")
assets_tbl = dbutils.widgets.get("assets_table")
matches_tbl = dbutils.widgets.get("matches_table")

# Thresholds
BLOCKER_THRESHOLD = 0.90  # ~14/16 hashes match
WARN_THRESHOLD = 0.75     # ~12/16 hashes match

print(f"Running Detection: {target_cat} vs {ref_cat}")

# UDF: MinHash Fingerprinting
# Generates a 'signature' of the schema (16 integers). 
# Tables with similar columns will have similar signatures.

@F.udf(ArrayType(IntegerType()))
def compute_minhash(col_names):
    if not col_names: return [0] * 16
    
    # 16 pairs of random coefficients (a, b)
    coeffs = [
        (1, 3), (3, 5), (5, 7), (7, 11), (11, 13), (13, 17), (17, 19), (19, 23),
        (23, 29), (29, 31), (31, 37), (37, 41), (41, 43), (43, 47), (47, 53), (53, 59)
    ]
    prime = 2038074743 
    
    signature = []
    for a, b in coeffs:
        min_val = float('inf')
        for name in col_names:
            h = hash(name)
            permuted = (a * h + b) % prime
            if permuted < min_val:
                min_val = permuted
        signature.append(int(min_val))
    return signature

# Smart Ingest
def get_schemas_to_scan(catalog, specific_schema=None):
    if specific_schema: return [specific_schema]
    try:
        rows = spark.sql(f"SHOW SCHEMAS IN {catalog}").collect()
        # Filter noise
        return [r.databaseName for r in rows 
                if r.databaseName.lower() not in ['information_schema', 'system']
                and not r.databaseName.lower().startswith(('tmp_', 'temp_', 'scratch_'))]
    except: return []

def ingest_catalog(catalog, schemas):
    if not schemas: return None
    schema_list_str = "', '".join(schemas)
    
    return spark.sql(f"""
    WITH col_agg AS (
        SELECT 
            table_catalog, table_schema, table_name,
            collect_set(lower(regexp_replace(column_name, '[^a-zA-Z0-9]', ''))) as col_names_norm,
            count(1) as col_count
        FROM system.information_schema.columns
        WHERE table_catalog = '{catalog}' AND table_schema IN ('{schema_list_str}')
        GROUP BY table_catalog, table_schema, table_name
    )
    SELECT 
        concat(t.table_catalog, '.', t.table_schema, '.', t.table_name) as full_name,
        t.table_catalog as catalog,
        t.table_schema as schema,
        t.table_name as object_name,
        c.col_names_norm,
        t.last_altered as last_modified
    FROM system.information_schema.tables t
    JOIN col_agg c 
      ON t.table_catalog = c.table_catalog 
      AND t.table_schema = c.table_schema 
      AND t.table_name = c.table_name
    WHERE t.table_catalog = '{catalog}' AND t.table_schema IN ('{schema_list_str}')
    """)

# 1. Run Ingest
tgt_schemas = get_schemas_to_scan(target_cat, target_sch if target_sch else None)
ref_schemas = get_schemas_to_scan(ref_cat)

df_tgt = ingest_catalog(target_cat, tgt_schemas)
df_ref = ingest_catalog(ref_cat, ref_schemas)

if not df_tgt or not df_ref:
    dbutils.notebook.exit("Insufficient data found to scan.")

# 2. Compute Signatures & Union
df_all_raw = df_tgt.unionByName(df_ref)

df_assets_processed = (
    df_all_raw
    .withColumn("schema_sig", compute_minhash(F.col("col_names_norm"))) # Returns Array<Int>
    .withColumn("run_id", F.lit(None).cast("string"))
    .withColumn("ingested_at", F.current_timestamp())
    .drop("col_names_norm")
)

# 3. Persist Assets (Overwrite/Merge)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {assets_tbl.split('.')[0]}")
df_assets_processed.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(assets_tbl)
print(f"Ingested {df_assets_processed.count()} assets.")

# COMMAND ----------
# DBTITLE 1, Step 2: LSH Matching & Scoring
# We reuse df_assets_processed to avoid reloading from Delta immediately

# 1. Split streams
df_t_source = df_assets_processed.filter(F.col("catalog") == target_cat).alias("t")
df_r_source = df_assets_processed.filter(F.col("catalog") == ref_cat).alias("r")

# 2. Explode Signatures (Blocking)
# "schema_sig" is Array<int>. We explode it to find overlaps.
df_t_exp = df_t_source.select(
    F.col("full_name").alias("t_name"), 
    F.col("schema_sig").alias("t_sig"), 
    F.explode("schema_sig").alias("bucket")
)
df_r_exp = df_r_source.select(
    F.col("full_name").alias("r_name"), 
    F.col("schema_sig").alias("r_sig"), 
    F.explode("schema_sig").alias("bucket")
)

# 3. Join on Bucket & Count Overlaps
# 16 buckets total. If they share a bucket, they are candidates.
candidates = (
    df_t_exp.join(df_r_exp, on="bucket", how="inner")
    .groupBy("t_name", "r_name")
    .agg(F.count("bucket").alias("shared_buckets"))
)

# 4. Score & Classify
NUM_PERMUTATIONS = 16

df_results = (
    candidates
    .withColumn("s_schema", F.col("shared_buckets") / F.lit(NUM_PERMUTATIONS))
    .withColumn("similarity", F.col("s_schema"))
    .withColumn(
        "policy_class",
        F.when(F.col("similarity") >= BLOCKER_THRESHOLD, "BLOCKER")
         .when(F.col("similarity") >= WARN_THRESHOLD, "WARN")
         .otherwise("INFO")
    )
    .withColumn(
        "explanation",
        F.concat(F.lit("Schema overlap approx "), F.round(F.col("s_schema") * 100, 1), F.lit("%"))
    )
    .withColumn("scored_at", F.current_timestamp())
    # Filter out noise
    .filter("policy_class != 'INFO'")
    .select("t_name", "r_name", "similarity", "policy_class", "explanation", "scored_at")
    .withColumnRenamed("t_name", "target_full_name")
    .withColumnRenamed("r_name", "reference_full_name")
)

# COMMAND ----------
# DBTITLE 1, Persist Matches & Report
df_results.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(matches_tbl)

print(f"Scoring complete. Found {df_results.count()} actionable matches.")
display(spark.table(matches_tbl).orderBy(F.desc("similarity")))