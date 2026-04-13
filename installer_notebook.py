# Databricks notebook source
# MAGIC %md
# MAGIC # ATLAS - Prerequisites Installer
# MAGIC This notebook provisions the necessary prerequisites for ATLAS without needing Terraform or complex CI/CD setups.
# MAGIC 
# MAGIC ### Instructions:
# MAGIC 1. Attach this notebook to any cluster.
# MAGIC 2. Fill out the widgets at the top.
# MAGIC 3. Click **Run All**.
# MAGIC 
# MAGIC *Note: You must have workspace admin privileges to create secret scopes, and catalog creation privileges to create catalogs.*

# COMMAND ----------

dbutils.widgets.text("secret_scope", "atlas-hub", "1. Secret Scope Name")
dbutils.widgets.text("catalog_name", "atlas_catalog", "2. UC Catalog Name")
dbutils.widgets.text("schema_name", "atlas", "3. UC Schema Name")
dbutils.widgets.text("volume_name", "gitops_requests", "4. UC Volume Name (for GitOps)")
dbutils.widgets.text("github_pat", "", "5. (Optional) GitHub PAT")

secret_scope = dbutils.widgets.get("secret_scope")
catalog_name = dbutils.widgets.get("catalog_name")
schema_name = dbutils.widgets.get("schema_name")
volume_name = dbutils.widgets.get("volume_name")
github_pat = dbutils.widgets.get("github_pat")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Create Secret Scope & Secrets

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

import secrets
import string

print(f"Ensuring secret scope '{secret_scope}' exists...")
try:
    scopes = [s.name for s in w.secrets.list_scopes()]
    if secret_scope not in scopes:
        w.secrets.create_scope(scope=secret_scope)
        print(f"✅ Created secret scope: {secret_scope}")
    else:
        print(f"✅ Secret scope '{secret_scope}' already exists.")
except Exception as e:
    print(f"⚠️ Could not check/create secret scope. If it doesn't exist, create it manually. Error: {e}")

# Generate a secure random password for Lakebase if it doesn't exist
try:
    w.secrets.get_secret(scope=secret_scope, key="lakebase-password")
    print("✅ lakebase-password already exists in secret scope.")
except Exception:
    print("Generating secure random password for Lakebase...")
    alphabet = string.ascii_letters + string.digits
    secure_pw = ''.join(secrets.choice(alphabet) for i in range(24))
    w.secrets.put_secret(scope=secret_scope, key="lakebase-password", string_value=secure_pw)
    print("✅ Generated and saved new lakebase-password")

if github_pat:
    w.secrets.put_secret(scope=secret_scope, key="github-pat", string_value=github_pat)
    print("✅ Saved github-pat")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Create Unity Catalog Infrastructure

# COMMAND ----------

print(f"Creating Catalog '{catalog_name}'...")
spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog_name}")

print(f"Creating Schema '{catalog_name}.{schema_name}'...")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog_name}.{schema_name}")

print(f"Creating Volume '{catalog_name}.{schema_name}.{volume_name}'...")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog_name}.{schema_name}.{volume_name}")

print("✅ Unity Catalog infrastructure ready!")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 🚀 Next Steps
# MAGIC 
# MAGIC You can now deploy ATLAS using GitHub Actions or the Databricks CLI!
# MAGIC 
# MAGIC Ensure 'gitops_volume_path' in your `databricks.yml` or GitHub Action variables is set correctly.

# COMMAND ----------

volume_path = f"/Volumes/{catalog_name}/{schema_name}/{volume_name}"
print(f"Ensure 'gitops_volume_path' is set to: {volume_path}")