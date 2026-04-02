#!/bin/bash
set -e

command -v databricks >/dev/null 2>&1 || {
  echo "Error: databricks CLI not found in PATH" >&2
  exit 1
}

echo "============================================"
echo "ATLAS Prerequisite Bootstrap Script"
echo "============================================"
echo "This script will set up the necessary Databricks resources for ATLAS."
echo ""

read -p "Enter Secret Scope name [default: atlas-hub]: " SECRET_SCOPE
SECRET_SCOPE=${SECRET_SCOPE:-atlas-hub}

echo "1. Creating Secret Scope: $SECRET_SCOPE..."
databricks secrets create-scope "$SECRET_SCOPE" || echo "Scope might already exist, continuing..."

echo ""
echo "2. Setting up Secrets..."
read -s -p "Enter Lakebase Database Password (press Enter to skip): " LAKEBASE_PW
echo ""
if [ -n "$LAKEBASE_PW" ]; then
  databricks secrets put-secret "$SECRET_SCOPE" lakebase-password --string-value "$LAKEBASE_PW"
fi

read -s -p "Enter GitHub Personal Access Token (press Enter to skip): " GITHUB_PAT
echo ""
if [ -n "$GITHUB_PAT" ]; then
  databricks secrets put-secret "$SECRET_SCOPE" github-pat --string-value "$GITHUB_PAT"
fi

echo ""
echo "3. Setting up Unity Catalog structure for GitOps..."
read -p "Enter Catalog name for GitOps [default: atlas_dev_catalog]: " CATALOG_NAME
CATALOG_NAME=${CATALOG_NAME:-atlas_dev_catalog}

read -p "Enter Schema name for GitOps [default: atlas]: " SCHEMA_NAME
SCHEMA_NAME=${SCHEMA_NAME:-atlas}

read -p "Enter Volume name for GitOps [default: gitops_requests]: " VOLUME_NAME
VOLUME_NAME=${VOLUME_NAME:-gitops_requests}

echo "Creating Catalog $CATALOG_NAME..."
databricks catalogs create "$CATALOG_NAME" || echo "Catalog might already exist, continuing..."

echo "Creating Schema $CATALOG_NAME.$SCHEMA_NAME..."
databricks schemas create "$SCHEMA_NAME" "$CATALOG_NAME" || echo "Schema might already exist, continuing..."

echo "Creating Volume $CATALOG_NAME.$SCHEMA_NAME.$VOLUME_NAME..."
databricks volumes create "$CATALOG_NAME" "$SCHEMA_NAME" "$VOLUME_NAME" VOLUME_TYPE_MANAGED || echo "Volume might already exist, continuing..."

echo ""
echo "============================================"
echo "Bootstrap complete!"
echo "Make sure to update databricks.yml with your Catalog and Schema paths if you changed them from the defaults."
echo "You can now run: ./deploy.sh dev"
echo "============================================"
